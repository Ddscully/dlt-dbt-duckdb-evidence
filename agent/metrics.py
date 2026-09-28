"""Totals and breakdowns of the retail metrics, by name, without writing SQL.

**A metric is defined once, in dbt's semantic layer**
(`dbt/models/marts/retail/_retail_metrics.yml`), and nowhere in Python. `dbt
parse` writes the definitions to `semantic_manifest.json`; MetricFlow's engine
compiles a request for them — metrics, a grouping, a filter — to SQL; and this
module runs that SQL on the caller's own read-only DuckDB connection. MetricFlow
never connects to anything here: its client is `RenderOnly`, which knows the
dialect and refuses to execute. `docs/decisions/0015-metrics-in-the-semantic-layer.md`
has what that was chosen over.

Three things this module does because MetricFlow does not, each measured:

* **Nothing checks that a summed column may be summed.** MetricFlow ignores the
  `additivity` labels; `agg: sum` over `unit_price` compiled and returned a
  figure. `tests/test_semantic_layer.py` holds the yml to the labels instead.
* **Distinct counts do not roll up**, so a grouped request's `all` row comes
  from a second, ungrouped request, never from adding the rows: customers by
  year sum to far more than the customers of all years. The table says so only
  where the rows do overcount: orders by year or by country add up, because an
  invoice falls on one day and in one country.
* **Float sums differ between runs** in about the ninth significant figure, so
  money is printed to 2 decimal places, and the digits a model quotes are
  stable.

**Years the data covers only in part are reported as they are**, and
`coverage_note` names them, for the loop to append verbatim: 2009 holds one
December and 2011 stops on 9 December, so "revenue by year" is not like for
like. Comparing periods fairly is `explain_change`'s job, which aligns them.

What a model may group by is `GROUP_BY` and nothing else: a customer id is
counted by `customers`, never a dimension.

Run:  uv run python -m agent.metrics net_revenue_gbp orders --by year
"""

from __future__ import annotations

import argparse
import calendar
import datetime as dt
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import duckdb
from metricflow.engine.metricflow_engine import MetricFlowEngine, MetricFlowQueryRequest
from metricflow.protocols.sql_client import SqlEngine
from metricflow.sql.render.duckdb_renderer import DuckDbSqlPlanRenderer
from metricflow_semantics.model.dbt_manifest_parser import (
    parse_manifest_from_dbt_generated_manifest,
)
from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup

from modern_data_stack.paths import dbt_semantic_manifest_path, warehouse_path

# The names a model groups by, and what MetricFlow calls them. A joined
# dimension is `<entity>__<dimension>`, `country__region`, not prefixed with the
# fact's own entity.
TIME_GRAINS = {
    "year": "metric_time__year",
    "quarter": "metric_time__quarter",
    "month": "metric_time__month",
}
GROUP_BY = {**TIME_GRAINS, "country": "country__country_name", "region": "country__region"}
# The dimensions `where` may filter on: their values are checked against the data
# before they reach the SQL.
FILTERS = {"country": "country__country_name", "region": "country__region"}
MAX_ROWS = 60


class RenderOnly:
    """As much of MetricFlow's `SqlClient` as `explain` needs: the dialect, and no connection."""

    sql_engine_type = SqlEngine.DUCKDB
    sql_plan_renderer = DuckDbSqlPlanRenderer()

    def query(self, *args, **kwargs):
        raise AssertionError("MetricFlow tried to execute SQL; this module runs it")

    execute = dry_run = query

    def close(self) -> None:
        pass

    def render_bind_parameter_key(self, bind_parameter_key: str) -> str:
        return f"${bind_parameter_key}"


@dataclass(frozen=True)
class SemanticLayer:
    manifest: dict  # semantic_manifest.json, as parsed
    engine: MetricFlowEngine

    @property
    def metrics(self) -> dict[str, dict]:
        return {metric["name"]: metric for metric in self.manifest["metrics"]}

    def measures(self, metric: str) -> list[tuple[dict, dict]]:
        """(semantic model, measure) for every measure `metric` reads."""
        names = {m["name"] for m in self.metrics[metric]["type_params"]["input_measures"]}
        return [
            (model, measure)
            for model in self.manifest["semantic_models"]
            for measure in model["measures"]
            if measure["name"] in names
        ]

    def is_distinct_count(self, metric: str) -> bool:
        """A count of distinct things, which may be counted again in each group."""
        spec = self.metrics[metric]
        return spec["type"] == "simple" and all(
            measure["agg"] == "count_distinct" for _, measure in self.measures(metric)
        )


def load_semantic_layer(path: str | Path | None = None) -> SemanticLayer:
    path = Path(path or dbt_semantic_manifest_path())
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist: run `just dbt-parse`")
    text = path.read_text()
    lookup = SemanticManifestLookup(parse_manifest_from_dbt_generated_manifest(text))
    return SemanticLayer(json.loads(text), MetricFlowEngine(lookup, RenderOnly()))


@dataclass(frozen=True)
class MetricTable:
    metrics: tuple[str, ...]
    group_by: tuple[str, ...]
    rows: tuple[tuple[tuple, tuple], ...]  # (group labels, metric values)
    total: tuple | None  # the ungrouped values, when grouped
    overcounted: tuple[str, ...]  # distinct counts whose rows add up to more than `all`
    years: tuple[int, int] | None
    coverage: tuple[dt.date, dt.date]
    partial: tuple[str, ...]  # "2011 (1 Jan to 9 Dec only)", one per partly covered period


def _compile(
    layer: SemanticLayer, metrics: Sequence[str], group_by: Sequence[str], where: list[str]
) -> str:
    names = [GROUP_BY[g] for g in group_by]
    request = MetricFlowQueryRequest.create(
        metric_names=list(metrics),
        group_by_names=names,
        where_constraints=where,
        order_by_names=names,
    )
    return layer.engine.explain(request).sql_statement.sql


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def filter_values(con: duckdb.DuckDBPyConnection, dimension: str) -> list[str]:
    """The values of a `where` dimension that have sales, as the tool accepts them."""
    column = FILTERS[dimension].split("__", 1)[1]
    rows = con.execute(
        f"""select distinct c.{column} from marts.dim_country c
            join marts.fct_retail_order_line f on f.country_iso3 = c.country_iso3
            order by 1"""
    ).fetchall()
    return [row[0] for row in rows]


def _coverage(
    con: duckdb.DuckDBPyConnection, layer: SemanticLayer, metrics: Sequence[str]
) -> tuple[dt.date, dt.date]:
    """The first and last day the metrics' facts hold, narrowest across them."""
    bounds = []
    for model in {id(m): m for metric in metrics for m, _ in layer.measures(metric)}.values():
        relation = model["node_relation"]
        time_dimension = model["defaults"]["agg_time_dimension"]
        column = next(
            d.get("expr") or d["name"] for d in model["dimensions"] if d["name"] == time_dimension
        )
        row = con.execute(
            f"select min({column}), max({column}) from {relation['schema_name']}.{relation['alias']}"
        ).fetchone()
        if row is None or row[0] is None:
            raise ValueError(f"{relation['schema_name']}.{relation['alias']} has no rows")
        bounds.append(row)
    return max(b[0] for b in bounds), min(b[1] for b in bounds)


def _bucket(grain: str, start: dt.date) -> tuple[str, dt.date]:
    """A time bucket's label and last day, from its first day."""
    if grain == "year":
        return str(start.year), dt.date(start.year, 12, 31)
    if grain == "quarter":
        last_month = start.month + 2
        end = dt.date(start.year, last_month, calendar.monthrange(start.year, last_month)[1])
        return f"{start.year}-Q{(start.month - 1) // 3 + 1}", end
    return f"{start:%Y-%m}", dt.date(
        start.year, start.month, calendar.monthrange(start.year, start.month)[1]
    )


def _day(day: dt.date) -> str:
    return f"{day.day} {day:%b}"


def _partial(
    label: str, start: dt.date, end: dt.date, coverage: tuple[dt.date, dt.date]
) -> str | None:
    first, last = max(start, coverage[0]), min(end, coverage[1])
    if (first, last) == (start, end):
        return None
    return f"{label} ({_day(first)} to {_day(last)} only)"


def query_metric(
    con: duckdb.DuckDBPyConnection,
    layer: SemanticLayer,
    metrics: Sequence[str],
    group_by: Sequence[str] = (),
    years: tuple[int, int] | None = None,
    where: Mapping[str, str] | None = None,
) -> MetricTable:
    """Metric values, grouped and filtered, with an `all` row and the periods covered in part."""
    metrics, group_by, where = tuple(metrics), tuple(group_by), dict(where or {})
    unknown = [m for m in metrics if m not in layer.metrics]
    if not metrics or unknown:
        raise ValueError(
            f"unknown metric {', '.join(unknown) or '(none given)'}; the metrics are "
            + ", ".join(layer.metrics)
        )
    bad = [g for g in group_by if g not in GROUP_BY]
    if bad:
        raise ValueError(f"cannot group by {', '.join(bad)}; choose from {', '.join(GROUP_BY)}")
    grains = [g for g in group_by if g in TIME_GRAINS]
    if len(grains) > 1:
        raise ValueError(f"group by one time grain at most, not {' and '.join(grains)}")

    coverage = _coverage(con, layer, metrics)
    constraints = []
    if years is not None:
        first, last = years
        if first > last:
            raise ValueError(f"years must be [first, last], not [{first}, {last}]")
        if first < coverage[0].year or last > coverage[1].year:
            raise ValueError(
                f"the data covers {coverage[0]} to {coverage[1]}; {first}-{last} is outside it"
            )
        constraints.append(
            "{{ TimeDimension('metric_time', 'day') }} "
            f"between '{first}-01-01' and '{last}-12-31'"
        )
    for dimension, value in where.items():
        if dimension not in FILTERS:
            raise ValueError(f"cannot filter on {dimension}; choose from {', '.join(FILTERS)}")
        allowed = filter_values(con, dimension)
        if value not in allowed:
            raise ValueError(
                f"no sales with {dimension} {value!r}; the values are {', '.join(allowed)}"
            )
        constraints.append(f"{{{{ Dimension('{FILTERS[dimension]}') }}}} = {_quote(value)}")

    result = con.execute(_compile(layer, metrics, group_by, constraints))
    records = result.fetchall()
    if len(records) > MAX_ROWS:
        raise ValueError(
            f"{len(records)} rows is too many to read; narrow it with years or a filter"
        )
    width = len(group_by)
    grain = grains[0] if grains else None
    time_index = group_by.index(grain) if grain else None
    rows, periods = [], {}
    for record in records:
        labels = list(record[:width])
        if grain is not None and time_index is not None:
            start = labels[time_index].date()
            labels[time_index], end = _bucket(grain, start)
            periods[labels[time_index]] = (start, end)
        rows.append((tuple(labels), tuple(record[width:])))
    if grain is None and years is not None:
        periods = {
            str(y): (dt.date(y, 1, 1), dt.date(y, 12, 31)) for y in range(years[0], years[1] + 1)
        }

    total, overcounted = None, ()
    if group_by:
        total = con.execute(_compile(layer, metrics, (), constraints)).fetchone()
        overcounted = tuple(
            metric
            for i, metric in enumerate(metrics)
            if layer.is_distinct_count(metric)
            and total is not None
            and sum(values[i] or 0 for _, values in rows) != (total[i] or 0)
        )
    partial = tuple(
        note
        for label, (start, end) in sorted(periods.items(), key=lambda item: item[1])
        if (note := _partial(label, start, end, coverage))
    )
    return MetricTable(
        metrics=metrics,
        group_by=group_by,
        rows=tuple(rows),
        total=tuple(total) if total is not None else None,
        overcounted=overcounted,
        years=years,
        coverage=coverage,
        partial=partial,
    )


def _format(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, int):
        return f"{value:,}"
    return f"{round(value, 2) + 0.0:,.2f}"  # + 0.0 turns -0.0 into 0.0


def render(table: MetricTable) -> str:
    """A plain-text table: the groups, one column per metric, then the `all` row."""
    if table.years is None:
        period = f"all dates, {table.coverage[0]} to {table.coverage[1]}"
    elif table.years[0] == table.years[1]:
        period = str(table.years[0])
    else:
        period = f"{table.years[0]} to {table.years[1]}"
    by = f" by {', '.join(table.group_by)}" if table.group_by else ""
    lines = [f"{', '.join(table.metrics)}{by}, {period}"]

    header = [*table.group_by, *table.metrics]
    body = [
        [
            *("(no country)" if label is None else str(label) for label in labels),
            *map(_format, values),
        ]
        for labels, values in table.rows
    ]
    if table.total is not None:
        body.append(["all", *[""] * (len(table.group_by) - 1), *map(_format, table.total)])
    if not body:
        body = [["—"] * len(header)]
    widths = [max(len(row[i]) for row in [header, *body]) for i in range(len(header))]
    labels = len(table.group_by)
    for row in [header, *body]:
        cells = [
            cell.ljust(width) if i < labels else cell.rjust(width)
            for i, (cell, width) in enumerate(zip(row, widths, strict=True))
        ]
        lines.append("  ".join(cells).rstrip())
    for metric in table.overcounted:
        lines.append(
            f'{metric} is a distinct count, and its rows add up to more than "all": '
            "one can be counted in several rows."
        )
    return "\n".join(lines)


def coverage_note(table: MetricTable) -> str | None:
    """The sentence an answer must carry when a period in it is covered only in part."""
    if not table.partial:
        return None
    return (
        f"Partly covered: {', '.join(table.partial)}. "
        "To compare periods, use explain_change, which aligns them."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("metrics", nargs="+")
    parser.add_argument("--by", nargs="*", default=[], choices=sorted(GROUP_BY))
    parser.add_argument("--years", nargs=2, type=int, metavar=("FIRST", "LAST"))
    parser.add_argument(
        "--where",
        action="append",
        default=[],
        metavar="DIMENSION=VALUE",
        help='e.g. --where "region=Europe & Central Asia"',
    )
    args = parser.parse_args()
    where = dict(item.split("=", 1) for item in args.where)
    layer = load_semantic_layer()
    # Read-only: fails while a build holds the file, by DuckDB's design.
    with duckdb.connect(warehouse_path(), read_only=True) as con:
        try:
            table = query_metric(
                con, layer, args.metrics, args.by, args.years and tuple(args.years), where
            )
        except ValueError as exc:
            parser.exit(2, f"error: {exc}\n")
    print(render(table))
    if note := coverage_note(table):
        print(f"\n{note}")


if __name__ == "__main__":
    main()
