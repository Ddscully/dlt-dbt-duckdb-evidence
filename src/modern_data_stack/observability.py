"""Turn the warehouse's own metadata into queryable tables.

Everything a pipeline like this knows about itself is already in the database —
dlt stamps `_dlt_load_id` on every raw row, dbt stores each failing test row in
its audit schema when `store_failures` is on, and `information_schema` knows the
shape of every layer. None of it is *queryable from a report*, because two of the
three need either dynamic SQL over a table list that isn't known until runtime or
a file outside the database. This module resolves both.

None of it is new instrumentation. A pipeline page costs a few dozen lines of SQL
over metadata three tools were already writing, which is worth knowing before
anyone proposes emitting metrics for it.

Landing tables and layer names come from the project; see
`transform/pipeline_status.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import polars as pl

DEFAULT_AUDIT_SCHEMA = "dbt_test__audit"


def _has_column(con: duckdb.DuckDBPyConnection, schema: str, table: str, column: str) -> bool:
    return bool(
        con.execute(
            """
            select 1 from information_schema.columns
            where table_schema = ? and table_name = ? and column_name = ?
            """,
            [schema, table, column],
        ).fetchone()
    )


def _period_span(
    con: duckdb.DuckDBPyConnection, schema: str, table: str, column: str
) -> tuple[int, int]:
    """(min, max) of a table's period column, or (None, None) if it has none."""
    if not _has_column(con, schema, table, column):
        return (None, None)
    return con.sql(f'select min({column}), max({column}) from "{schema}"."{table}"').fetchone()


def build_sources(
    con: duckdb.DuckDBPyConnection,
    source_tables: tuple[str, ...],
    raw_schema: str = "raw",
    period_column: str = "year",
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
        if not _has_column(con, raw_schema, table, "_dlt_load_id"):
            continue
        n, loaded_at = con.sql(
            f"""
            select
                count(*),
                to_timestamp(max(cast(_dlt_load_id as double)))
            from {raw_schema}."{table}"
            """
        ).fetchone()
        period_min, period_max = _period_span(con, raw_schema, table, period_column)
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

    `exclude_prefix` keeps this module's own output out of the inventory.
    Without it the inventory inventories itself, and the table count depends on
    whether it has run before — 10 on a first build, 13 on every later one, for
    no change in the warehouse.

    An empty `exclude_prefix` means "exclude nothing", and has to drop the
    predicate rather than pass it: `not like '' || '%'` is `not like '%'`, which
    matches no row at all. That returns an empty inventory, which surfaces much
    later and much less legibly as an empty frame out of `write_status`.
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
        n = con.sql(f'select count(*) from "{schema}"."{table}"').fetchone()[0]
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


def manifest_tests(manifest_path: str) -> dict[str, dict]:
    """Map audit-table name -> {test type, model it guards, column}.

    The audit table is named after the test's `alias`, and dbt **truncates and
    hashes** an alias longer than 63 characters
    (`dbt_utils_accepted_range_fct_c_1c6718ee2bb...`), so the table name on its
    own is not a readable label. The manifest is where the real name, the
    attached model and the tested column live — and it's gitignored, so an absent
    manifest degrades to bare table names rather than failing.
    """
    path = Path(manifest_path)
    if not path.exists():
        return {}

    manifest = json.loads(path.read_text())
    out: dict[str, dict] = {}
    for node in manifest.get("nodes", {}).values():
        if node.get("resource_type") != "test":
            continue
        metadata = node.get("test_metadata") or {}
        attached = node.get("attached_node") or ""
        out[node["alias"]] = {
            "test_name": node["name"],
            "test_type": metadata.get("name") or "singular",
            # `model.<project>.fct_emissions_energy` -> the last segment
            "tested_model": attached.rsplit(".", 1)[-1] or None,
            "tested_column": (metadata.get("kwargs") or {}).get("column_name"),
        }
    return out


def build_tests(
    con: duckdb.DuckDBPyConnection,
    manifest_path: str,
    audit_schema: str = DEFAULT_AUDIT_SCHEMA,
) -> pl.DataFrame:
    """One row per dbt test, with the number of rows currently failing it.

    Requires `+store_failures: true` project-wide, so each test leaves a table in
    the audit schema holding the rows it rejected. An empty table is a *passing*
    test — which is why this counts rather than checks existence, and why a green
    pipeline produces a table of zeroes.
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

    rows = []
    for table in audit_tables:
        failing = con.sql(f'select count(*) from {audit_schema}."{table}"').fetchone()[0]
        meta = catalogue.get(table, {})
        rows.append(
            {
                "test_name": meta.get("test_name") or table,
                "test_type": meta.get("test_type"),
                "tested_model": meta.get("tested_model"),
                "tested_column": meta.get("tested_column"),
                "failing_rows": failing,
                "status": "fail" if failing else "pass",
                "audit_table": f"{audit_schema}.{table}",
            }
        )
    return pl.DataFrame(rows)


def write_status(
    con: duckdb.DuckDBPyConnection,
    frames: dict[str, pl.DataFrame],
    schema: str = "analytics",
) -> dict[str, int]:
    """Write each frame to `<schema>.<name>`, replacing it. Returns rows written.

    Takes a connection rather than a path: the frames were read through one, and
    DuckDB allows a single writer, so re-opening the file here would be a lock to
    trip over for no benefit.
    """
    con.sql(f"create schema if not exists {schema}")
    for name, frame in frames.items():
        con.register("frame_df", frame)  # DuckDB reads Polars frames directly
        con.sql(f"create or replace table {schema}.{name} as select * from frame_df")
        con.unregister("frame_df")
    return {name: frame.height for name, frame in frames.items()}
