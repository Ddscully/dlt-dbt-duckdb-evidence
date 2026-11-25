"""Totals and breakdowns of the semantic layer's metrics, by name, without writing SQL.

**A metric is defined once, in dbt's semantic layer**, in the yml beside the
models it reads (`dbt/models/marts/retail/_retail_metrics.yml`), and nowhere in
Python. `dbt
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

**What a request may group or filter by comes from the yml**, and nothing else:
a time grain no finer than its table's own (a yearly table has no quarters), and
a dimension the yml offers with `config: meta: {offered_as: <name>}`. So a
customer id is counted by `customers`, never a dimension. A `where` value is
checked against the values the table has rows for, asked with every metric that
reads the table: `returns_gbp` alone reaches 31 of the 40 countries with sales,
and returns in a country with none are 0.00, not a refusal.

**One table per request.** MetricFlow joins any two metrics on the date: CO2 and
revenue by year gave 277 rows, with revenue 0.00 in 1750.

**Coverage is where each measure's column has a value**, widened to whole
periods of the table's grain: a country-year row exists when any source reports
the year, so the rows run to 2026 where `co2_mt` stops in 2024, and a yearly row
is the whole year's figure.

**`top` ranks one breakdown by place** by the first metric, and says how many
groups it left out. It ranks after the overcount is judged on every row: the
largest groups can sum to a distinct count's total while all of them exceed it.

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
from metricflow_semantics.errors.error_classes import InformativeUserError
from metricflow_semantics.model.dbt_manifest_parser import (
    parse_manifest_from_dbt_generated_manifest,
)
from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup

from modern_data_stack.db import row as read_row
from modern_data_stack.paths import dbt_semantic_manifest_path, warehouse_path

# The time grains a request may group by, and what MetricFlow calls them. The
# other groupings are the dimensions the yml offers (`SemanticLayer.offered`); a
# joined one is `<entity>__<dimension>`, `country__region`, not prefixed with the
# fact's own entity.
TIME_GRAINS = {
    "year": "metric_time__year",
    "quarter": "metric_time__quarter",
    "month": "metric_time__month",
}
# MetricFlow's granularities, finest first: a table can be grouped by its own and coarser.
GRAINS = ("day", "week", "month", "quarter", "year")
MAX_ROWS = 60


def offered_as(dimension: dict) -> str | None:
    """The name `query_metric` offers a semantic-model dimension as, if the yml offers it."""
    return ((dimension.get("config") or {}).get("meta") or {}).get("offered_as")


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

    def models(self, metrics: Sequence[str]) -> list[dict]:
        """The semantic models whose measures `metrics` read, in manifest order."""
        names = {model["name"] for metric in metrics for model, _ in self.measures(metric)}
        return [model for model in self.manifest["semantic_models"] if model["name"] in names]

    def grain(self, model: dict) -> str:
        """A semantic model's time granularity: that of its aggregation time dimension."""
        name = model["defaults"]["agg_time_dimension"]
        dimension = next(d for d in model["dimensions"] if d["name"] == name)
        return dimension["type_params"]["time_granularity"]

    def time_grains(self, model: dict) -> list[str]:
        """The `TIME_GRAINS` no finer than the model's own grain."""
        finest = GRAINS.index(self.grain(model))
        return [grain for grain in TIME_GRAINS if GRAINS.index(grain) >= finest]

    def offered(self, metrics: Sequence[str] | None = None) -> dict[str, str]:
        """query_metric's name -> MetricFlow's, for what `metrics` can be grouped or filtered by.

        The time grains first, then each dimension the yml offers with
        `config.meta.offered_as`, sorted by name, that MetricFlow can reach from
        `metrics`. With no metrics: every name any metric offers, for the schema.
        """
        if metrics is None:
            tables = [model for model in self.manifest["semantic_models"] if model["measures"]]
            grains = {grain for table in tables for grain in self.time_grains(table)}
            reachable = None
        else:
            grains = set(TIME_GRAINS)
            for table in self.models(metrics):
                grains &= set(self.time_grains(table))
            reachable = {
                dimension.dunder_name
                for dimension in self.engine.list_dimensions(metric_names=list(metrics))
            }
        dimensions = {}
        for model in self.manifest["semantic_models"]:
            primary = next((e["name"] for e in model["entities"] if e["type"] == "primary"), None)
            if primary is None:
                continue
            for dimension in model["dimensions"]:
                name, ref = offered_as(dimension), f"{primary}__{dimension['name']}"
                if name and (reachable is None or ref in reachable):
                    dimensions[name] = ref
        times = {grain: ref for grain, ref in TIME_GRAINS.items() if grain in grains}
        return times | dict(sorted(dimensions.items()))


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
    refs: tuple[str, ...] = ()  # MetricFlow's name for each of `group_by`
    ranked: tuple[int, int] | None = None  # (groups kept, groups there were), with `top`


def _compile(
    layer: SemanticLayer, metrics: Sequence[str], refs: Sequence[str], where: list[str]
) -> str:
    names = list(refs)
    request = MetricFlowQueryRequest.create(
        metric_names=list(metrics),
        group_by_names=names,
        where_constraints=where,
        order_by_names=names,
    )
    try:
        return layer.engine.explain(request).sql_statement.sql
    except InformativeUserError as exc:
        # MetricFlow's refusal of a request, as this module's own: a caller
        # catches `ValueError`, and the tool sends it back to the model.
        raise ValueError(f"MetricFlow cannot answer that request. {exc}") from None


def _run(
    con: duckdb.DuckDBPyConnection,
    layer: SemanticLayer,
    metrics: Sequence[str],
    refs: Sequence[str],
    where: list[str],
) -> list[tuple]:
    """The request's rows, as the caller ordered them: the groups, then the metrics.

    MetricFlow orders the columns itself — the time grain first, and the metrics
    by how it plans them, not as they were asked for — so each is read by name.
    """
    result = con.execute(_compile(layer, metrics, refs, where))
    position = {column[0]: i for i, column in enumerate(result.description)}
    order = [position[name] for name in (*refs, *metrics)]
    return [tuple(record[i] for i in order) for record in result.fetchall()]


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def filter_values(
    con: duckdb.DuckDBPyConnection, layer: SemanticLayer, metrics: Sequence[str], dimension: str
) -> list[str]:
    """The values of a `where` dimension that the metrics' table has rows for.

    Asked with every metric that reads the same table, not only those requested:
    `returns_gbp` alone reaches 31 of the 40 countries with sales, and returns
    in a country with sales and none returned are 0.00, not a refusal.
    """
    tables = layer.models(metrics)
    siblings = [metric for metric in layer.metrics if layer.models([metric]) == tables]
    ref = layer.offered(metrics)[dimension]
    try:
        sql = layer.engine.explain_get_dimension_values(
            metric_names=siblings, get_group_by_values=ref
        ).sql_statement.sql
    except InformativeUserError as exc:
        raise ValueError(f"MetricFlow cannot list {dimension}. {exc}") from None
    return sorted({row[0] for row in con.execute(sql).fetchall() if row[0] is not None})


def _period(grain: str, day: dt.date) -> tuple[dt.date, dt.date]:
    """The first and last day of the `grain` period holding `day`; a day is its own."""
    if isinstance(day, dt.datetime):
        day = day.date()
    if grain == "year":
        return dt.date(day.year, 1, 1), dt.date(day.year, 12, 31)
    if grain in ("quarter", "month"):
        month = 3 * ((day.month - 1) // 3) + 1 if grain == "quarter" else day.month
        start = dt.date(day.year, month, 1)
        return start, _bucket(grain, start)[1]
    return day, day


def _coverage(
    con: duckdb.DuckDBPyConnection, layer: SemanticLayer, metrics: Sequence[str]
) -> tuple[dt.date, dt.date]:
    """The first and last day the metrics' measures have values, narrowest across them.

    Read from each measure's own column, not from the table's rows, and widened to
    whole periods of the table's grain (`_period`).
    """
    bounds = []
    for model in layer.models(metrics):
        relation = model["node_relation"]
        table = f"{relation['schema_name']}.{relation['alias']}"
        time_dimension = model["defaults"]["agg_time_dimension"]
        time = next(
            d.get("expr") or d["name"] for d in model["dimensions"] if d["name"] == time_dimension
        )
        grain = layer.grain(model)
        columns = {
            measure.get("expr") or measure["name"]
            for metric in metrics
            for read_from, measure in layer.measures(metric)
            if read_from["name"] == model["name"]
        }
        for column in sorted(columns):
            first, last = read_row(
                con, f"select min({time}), max({time}) from {table} where {column} is not null"
            )
            if first is None:
                raise ValueError(f"{table} has no {column}")
            bounds.append((_period(grain, first)[0], _period(grain, last)[1]))
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
    top: int | None = None,
) -> MetricTable:
    """Metric values, grouped and filtered, with an `all` row and the periods covered in part."""
    # A name given twice is answered once: MetricFlow refuses a duplicate, and
    # a model that repeats a metric is not asking for two columns of it.
    metrics, group_by = tuple(dict.fromkeys(metrics)), tuple(dict.fromkeys(group_by))
    where = dict(where or {})
    unknown = [m for m in metrics if m not in layer.metrics]
    if not metrics or unknown:
        raise ValueError(
            f"unknown metric {', '.join(unknown) or '(none given)'}; the metrics are "
            + ", ".join(sorted(layer.metrics))
        )
    tables = layer.models(metrics)
    if len(tables) > 1:
        raise ValueError(
            f"{', '.join(metrics)} come from different tables "
            f"({', '.join(table['name'] for table in tables)}): ask for each table's "
            "metrics in a separate call"
        )
    offered = layer.offered(metrics)
    bad = [g for g in group_by if g not in offered]
    if bad:
        raise ValueError(f"cannot group by {', '.join(bad)}; choose from {', '.join(offered)}")
    grains = [g for g in group_by if g in TIME_GRAINS]
    if len(grains) > 1:
        raise ValueError(f"group by one time grain at most, not {' and '.join(grains)}")
    if top is not None:
        if isinstance(top, bool) or not isinstance(top, int) or top < 1:
            raise ValueError(f"top is a whole number of groups, 1 or more, not {top!r}")
        if not group_by or grains:
            raise ValueError(
                "top ranks the groups of one breakdown by place: give group_by without "
                "year, quarter or month, and choose the period with years"
            )
        if top > MAX_ROWS:
            raise ValueError(f"top is at most {MAX_ROWS}")

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
        # At the table's own grain: a yearly table has no `day`, and MetricFlow
        # refuses a filter finer than the grain.
        constraints.append(
            "{{ TimeDimension('metric_time', '" + layer.grain(tables[0]) + "') }} "
            f"between '{first}-01-01' and '{last}-12-31'"
        )
    filters = [name for name in offered if name not in TIME_GRAINS]
    for dimension, value in where.items():
        if dimension not in filters:
            raise ValueError(f"cannot filter on {dimension}; choose from {', '.join(filters)}")
        allowed = filter_values(con, layer, metrics, dimension)
        if value not in allowed:
            # Quoted, because a value can hold a comma: "Hong Kong SAR, China".
            choices = ", ".join(json.dumps(name, ensure_ascii=False) for name in allowed)
            raise ValueError(f"no rows with {dimension} {value!r}; the values are {choices}")
        constraints.append(f"{{{{ Dimension('{offered[dimension]}') }}}} = {_quote(value)}")

    refs = tuple(offered[g] for g in group_by)
    records = _run(con, layer, metrics, refs, constraints)
    if top is None and len(records) > MAX_ROWS:
        rank = "" if grains else ", or rank them with top"
        raise ValueError(
            f"{len(records)} rows is too many to read; narrow it with years or a filter{rank}"
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
        total = next(iter(_run(con, layer, metrics, (), constraints)), None)
        overcounted = tuple(
            metric
            for i, metric in enumerate(metrics)
            if layer.is_distinct_count(metric)
            and total is not None
            and sum(values[i] or 0 for _, values in rows) != (total[i] or 0)
        )
    # Ranked only now, after the overcount is judged on every row: the largest
    # groups can sum to a distinct count's total while all of them exceed it.
    ranked = None
    if top is not None:
        ranked = (min(top, len(rows)), len(rows))
        rows = sorted(rows, key=_rank)[:top]
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
        refs=refs,
        ranked=ranked,
    )


def _rank(row: tuple[tuple, tuple]) -> tuple:
    """Largest first metric first, a missing one last, ties by label."""
    labels, values = row
    first = values[0]
    return (first is None, -(first or 0), [str(label) for label in labels])


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
    # "top 3 of 3" would promise groups left out where there are none.
    ranked, left_out = "", None
    if table.ranked is not None:
        kept, there = table.ranked
        if kept < there:
            ranked, left_out = f", top {kept} of {there} by {table.metrics[0]}", table.ranked
        else:
            ranked = f", ranked by {table.metrics[0]}"
    lines = [f"{', '.join(table.metrics)}{by}, {period}{ranked}"]

    # A null label is a row whose key found nothing to join: under `country` or
    # `region` alike, a line with no country.
    missing = [f"(no {ref.split('__', 1)[0]})" for ref in table.refs]
    header = [*table.group_by, *table.metrics]
    body = [
        [
            *(
                (missing[i] if i < len(missing) else "(none)") if label is None else str(label)
                for i, label in enumerate(labels)
            ),
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
    if left_out is not None:
        kept, there = left_out
        lines.append(f'"all" is the total over all {there} groups, not only the {kept} shown.')
    # "can": judged on every group, so under `top` the rows shown may not exceed "all".
    for metric in table.overcounted:
        lines.append(
            f'{metric} is a distinct count, and its rows can add up to more than "all": '
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
    # No `choices`: what a metric can be grouped by depends on its table, and
    # query_metric refuses anything else with the list of what it can.
    parser.add_argument("--by", nargs="*", default=[], help="a time grain or an offered name")
    parser.add_argument("--years", nargs=2, type=int, metavar=("FIRST", "LAST"))
    parser.add_argument(
        "--where",
        action="append",
        default=[],
        metavar="DIMENSION=VALUE",
        help='e.g. --where "region=Europe & Central Asia"',
    )
    parser.add_argument("--top", type=int, help="keep the N largest groups by the first metric")
    args = parser.parse_args()
    where = dict(item.split("=", 1) for item in args.where)
    layer = load_semantic_layer()
    # Read-only: fails while a build holds the file, by DuckDB's design.
    with duckdb.connect(warehouse_path(), read_only=True) as con:
        try:
            table = query_metric(
                con,
                layer,
                args.metrics,
                args.by,
                args.years and tuple(args.years),
                where,
                top=args.top,
            )
        except ValueError as exc:
            parser.exit(2, f"error: {exc}\n")
    print(render(table))
    if note := coverage_note(table):
        print(f"\n{note}")


if __name__ == "__main__":
    main()
