"""Turn the warehouse's own metadata into queryable tables.

No new instrumentation: dlt stamps `_dlt_load_id` on every raw row, dbt stores
failing test rows when `store_failures` is on, and `information_schema` knows
every layer's shape. What a report cannot do is the dynamic SQL over a runtime
table list, or read dbt's artifacts outside the database — this module does both.

Landing tables and layer names come from the project; see
`transform/pipeline_status.py`.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import duckdb
import polars as pl
from polars.datatypes import DataTypeClass

from .db import qualify, row, scalar

DEFAULT_AUDIT_SCHEMA = "dbt_test__audit"

# dbt's own default: a test fails on the number of rows it returned. Tests are
# free to override it, and `build_tests` reads each one's from the manifest.
DEFAULT_FAIL_CALC = "count(*)"


def _has_column(
    con: duckdb.DuckDBPyConnection,
    schema: str,
    table: str,
    column: str,
    database: str | None = None,
) -> bool:
    """Whether `column` is on `<database>.<schema>.<table>`.

    Pass `database` for a schema in an attached catalog (`lakehouse.raw`):
    without the `table_catalog` filter, `information_schema.columns` matches a
    `raw` schema in any attached database.
    """
    params = {"schema": schema, "table": table, "column": column}
    clause = ""
    if database is not None:
        # Clause and binding together: DuckDB rejects a named parameter the
        # statement does not mention.
        clause = " and table_catalog = $database"
        params["database"] = database
    return bool(
        con.execute(
            f"""
            select 1 from information_schema.columns
            where table_schema = $schema and table_name = $table
              and column_name = $column{clause}
            """,
            params,
        ).fetchone()
    )


def _period_span(
    con: duckdb.DuckDBPyConnection,
    schema: str,
    table: str,
    column: str,
    database: str | None = None,
) -> tuple[int | None, int | None]:
    """(min, max) of a table's period column; (None, None) with no such column
    or no rows."""
    if not _has_column(con, schema, table, column, database):
        return (None, None)
    lo, hi = row(
        con, f"select min({column}), max({column}) from {qualify(database, schema, table)}"
    )
    return (lo, hi)


def build_sources(
    con: duckdb.DuckDBPyConnection,
    source_tables: tuple[str, ...],
    raw_schema: str = "raw",
    period_column: str = "year",
    raw_database: str | None = None,
) -> pl.DataFrame:
    """Row counts, period span and load time for each dlt landing table.

    Freshness comes from `_dlt_load_id`, which dlt stamps as a **unix epoch in a
    varchar column** — the same value `dbt source freshness` reads. It records
    when *this pipeline* loaded the data, not when the publisher released it, so
    a stale timestamp here means the pipeline stopped running, not that the
    upstream stopped publishing.
    """
    rows = []
    for table in source_tables:
        if not _has_column(con, raw_schema, table, "_dlt_load_id", raw_database):
            continue
        n, loaded_at = row(
            con,
            f"""
            select
                count(*),
                to_timestamp(max(cast(_dlt_load_id as double)))
            from {qualify(raw_database, raw_schema, table)}
            """,
        )
        period_min, period_max = _period_span(con, raw_schema, table, period_column, raw_database)
        rows.append(
            {
                "source_table": f"{raw_schema}.{table}",
                "rows": n,
                "year_min": period_min,
                "year_max": period_max,
                "loaded_at": loaded_at,
            }
        )
    return pl.DataFrame(rows).sort("source_table")


def build_tables(
    con: duckdb.DuckDBPyConnection,
    layers: tuple[str, ...],
    exclude_prefix: str = "pipeline_",
    period_column: str = "year",
) -> pl.DataFrame:
    """Row counts and period spans for every table in the modelled layers.

    `exclude_prefix` keeps this module's own output out of the inventory, which
    would otherwise count itself from the second run on. An empty prefix drops
    the predicate: `not like '%'` would match nothing.
    """
    predicate = ""
    params: dict[str, object] = {"layers": list(layers)}
    if exclude_prefix:
        predicate = "and table_name not like $prefix || '%' escape '\\'"
        params["prefix"] = exclude_prefix.replace("_", "\\_")

    listed = con.execute(
        f"""
        select table_schema, table_name
        from information_schema.tables
        where table_schema in (select unnest($layers))
          {predicate}
        order by table_schema, table_name
        """,
        params,
    ).fetchall()

    rows = []
    for schema, table in listed:
        n = scalar(con, f'select count(*) from "{schema}"."{table}"')
        period_min, period_max = _period_span(con, schema, table, period_column)
        rows.append(
            {
                "layer": schema,
                "table_name": f"{schema}.{table}",
                "rows": n,
                "year_min": period_min,
                "year_max": period_max,
            }
        )
    return pl.DataFrame(rows)


def node_display_name(unique_id: str, nodes: dict[str, dict]) -> str | None:
    """Readable name for a node id.

    The node's `alias` — the relation that ran — because a versioned model's id
    ends `.v1`, and a test's in a hash. The id's last segment is the fallback,
    for ids (such as sources) that are not in `nodes`.
    """
    node = nodes.get(unique_id) or {}
    return node.get("alias") or unique_id.rsplit(".", 1)[-1] or None


def manifest_nodes(manifest_path: str) -> dict[str, dict]:
    """The manifest's node map, or `{}` when there is no manifest (`dbt/target/`
    is gitignored, so a fresh clone has none)."""
    path = Path(manifest_path)
    if not path.exists():
        return {}
    return json.loads(path.read_text()).get("nodes", {})


def manifest_tests(manifest_path: str) -> dict[str, dict]:
    """Map audit-table name -> {test type, model it guards, column, fail_calc, severity}.

    The audit table is named after the test's `alias`, which dbt truncates and
    hashes past 63 characters, so the manifest supplies the readable name, model
    and column. With no manifest this is empty and callers fall back to table
    names. `fail_calc` and `severity` are what `build_tests` scores with.
    """
    nodes = manifest_nodes(manifest_path)
    out: dict[str, dict] = {}
    for node in nodes.values():
        if node.get("resource_type") != "test":
            continue
        metadata = node.get("test_metadata") or {}
        attached = node.get("attached_node") or ""
        config = node.get("config") or {}
        out[node["alias"]] = {
            "test_name": node["name"],
            "test_type": metadata.get("name") or "singular",
            "tested_model": node_display_name(attached, nodes),
            "tested_column": (metadata.get("kwargs") or {}).get("column_name"),
            "fail_calc": config.get("fail_calc") or DEFAULT_FAIL_CALC,
            # dbt writes this as "ERROR"/"WARN", but a yml can spell it lowercase.
            "severity": (config.get("severity") or "error").lower(),
        }
    return out


def build_tests(
    con: duckdb.DuckDBPyConnection,
    manifest_path: str,
    audit_schema: str = DEFAULT_AUDIT_SCHEMA,
) -> pl.DataFrame:
    """One row per dbt test, with the number of rows currently failing it.

    Requires `+store_failures: true`, so each test leaves a table of the rows it
    rejected. The verdict is the test's `fail_calc` over that table, as dbt
    computes it — not `count(*)`: `dbt_utils.equal_rowcount` stores a one-row
    summary whether it passes or fails, so counting rows would score it as
    failing on a green build. With no manifest, `count(*)` (dbt's default).
    """
    audit_tables = [
        row[0]
        for row in con.execute(
            """
            select table_name from information_schema.tables
            where table_schema = ?
            order by table_name
            """,
            [audit_schema],
        ).fetchall()
    ]
    catalogue = manifest_tests(manifest_path)
    if catalogue:
        # Drop audit tables the manifest does not name: dbt never removes one
        # whose test is gone (renaming a model orphans all of them), and an empty
        # orphan would score as a passing test. Only when a manifest is present —
        # without one nothing matches and the table would empty.
        audit_tables = [table for table in audit_tables if table in catalogue]

    rows = []
    for table in audit_tables:
        meta = catalogue.get(table, {})
        fail_calc = meta.get("fail_calc") or DEFAULT_FAIL_CALC
        severity = meta.get("severity") or "error"
        # `sum(...)` over an empty table is null, where `count(*)` would be 0.
        failing = scalar(con, f'select coalesce({fail_calc}, 0) from {audit_schema}."{table}"')
        rows.append(
            {
                "test_name": meta.get("test_name") or table,
                "test_type": meta.get("test_type"),
                "tested_model": meta.get("tested_model"),
                "tested_column": meta.get("tested_column"),
                "severity": severity,
                "failing_rows": int(failing),
                # A warn-severity test with failures is `warn`: dbt does not fail
                # the build on it.
                "status": ("fail" if severity == "error" else "warn") if failing else "pass",
                "audit_table": f"{audit_schema}.{table}",
            }
        )
    return pl.DataFrame(rows)


# The shape `build_runs` returns, stated so an empty result still has columns:
# a column-less frame's first append would create a table every later append
# fails against. `DataTypeClass | pl.DataType` because `pl.String` is a class and
# `pl.Datetime("us")` an instance; Polars' own union for both is private.
RUN_COLUMNS: dict[str, DataTypeClass | pl.DataType] = {
    "invocation_id": pl.String,
    "invocation_started_at": pl.Datetime("us"),
    "dbt_command": pl.String,
    "dbt_version": pl.String,
    "unique_id": pl.String,
    "resource_type": pl.String,
    "node_name": pl.String,
    "status": pl.String,
    "execution_time_s": pl.Float64,
    "compile_time_s": pl.Float64,
    "execute_time_s": pl.Float64,
}


def _phase_seconds(timing: list[dict], phase: str) -> float | None:
    """Seconds spent in one of dbt's two timing phases, or None if absent.

    Kept beside `execution_time` because the total hides the split, and the two
    phases do not sum to it — dbt counts work outside both.
    """
    for entry in timing or []:
        if entry.get("name") != phase:
            continue
        started, completed = entry.get("started_at"), entry.get("completed_at")
        if not started or not completed:
            return None
        return (_parse_ts(completed) - _parse_ts(started)).total_seconds()
    return None


def _parse_ts(value: str) -> datetime:
    """dbt writes RFC 3339 with a trailing `Z`, which `fromisoformat` reads on
    Python 3.11+ (no `.replace("Z", "+00:00")` needed)."""
    return datetime.fromisoformat(value)


def build_runs(run_results_path: str, nodes: dict[str, dict] | None = None) -> pl.DataFrame:
    """One row per node in the dbt invocation `run_results.json` describes.

    No row counts: dbt-duckdb reports `rows_affected` for seeds only, so the
    artifact does not know them; `pipeline_tables` measures them instead.

    `nodes` (the manifest's node map) is optional because the manifest may be
    absent; without it versioned nodes are mislabelled — see
    `node_display_name`. An absent artifact returns the empty frame: a warehouse
    no dbt build has run against is a real state.
    """
    path = Path(run_results_path)
    if not path.exists():
        return pl.DataFrame(schema=RUN_COLUMNS)

    payload = json.loads(path.read_text())
    metadata = payload.get("metadata") or {}
    # Start time, not `generated_at` (written at the end), so runs order by
    # when they began.
    started = metadata.get("invocation_started_at") or metadata.get("generated_at")
    common = {
        "invocation_id": metadata.get("invocation_id"),
        "invocation_started_at": _parse_ts(started).replace(tzinfo=None) if started else None,
        # Any dbt command overwrites the artifact, so a row set means little
        # without the command that wrote it (a unit-test run vs a build).
        "dbt_command": (payload.get("args") or {}).get("which"),
        "dbt_version": metadata.get("dbt_version"),
    }

    nodes = nodes or {}
    rows = []
    for result in payload.get("results") or []:
        unique_id = result.get("unique_id") or ""
        timing = result.get("timing") or []
        rows.append(
            {
                **common,
                "unique_id": unique_id,
                # A result carries no `resource_type`; the id's first segment is it.
                "resource_type": unique_id.split(".")[0] or None,
                "node_name": node_display_name(unique_id, nodes),
                "status": result.get("status"),
                "execution_time_s": result.get("execution_time"),
                "compile_time_s": _phase_seconds(timing, "compile"),
                "execute_time_s": _phase_seconds(timing, "execute"),
            }
        )
    return pl.DataFrame(rows, schema=RUN_COLUMNS)
