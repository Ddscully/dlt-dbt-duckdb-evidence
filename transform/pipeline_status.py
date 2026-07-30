"""Pipeline observability: turn the warehouse's own metadata into queryable tables.

Everything the pipeline knows about itself is already in the DuckDB file — dlt
stamps `_dlt_load_id` on every raw row, dbt stores each failing test row in
`dbt_test__audit`, and `information_schema` knows the shape of every layer. None
of it is *queryable from a report*, because two of the three need either dynamic
SQL over a variable table list or a file outside the database.

This module resolves both and writes three flat tables into `analytics`:

* `pipeline_sources` — one row per dlt landing table: rows, span, when it loaded.
* `pipeline_tables`  — one row per table in every modelled layer: rows, year span.
* `pipeline_tests`   — one row per dbt test: what it guards and how many rows
  are currently failing it.

`reports/pages/pipeline.md` renders the three. They are a *snapshot* written at
run time, not a history — nothing here accumulates across runs the way
`history.snap_co2_estimates` does.

Run:  uv run python -m transform.pipeline_status
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import duckdb
import polars as pl

from ingest.pipeline import DUCKDB_PATH, REPO_ROOT

# dbt writes the manifest into the gitignored `dbt/target/`, so this is only
# present after a `dbt build`/`dbt parse`. Absent, the test inventory falls back
# to whatever audit tables exist — see `build_tests`.
MANIFEST_PATH = os.environ.get("DBT_MANIFEST_PATH") or str(
    REPO_ROOT / "dbt" / "target" / "manifest.json"
)

# The schemas that make up the modelled warehouse, in pipeline order. `raw` is
# covered separately by `build_sources` (it has freshness, these don't) and
# dbt's own bookkeeping schemas are deliberately absent.
LAYERS = ("staging", "marts", "analytics", "history")

# dlt's landing tables, minus its internal `_dlt_*` bookkeeping.
SOURCE_TABLES = ("owid_co2", "owid_energy", "wb_country", "wb_wdi", "eu_elec_prices")


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


def _year_span(con: duckdb.DuckDBPyConnection, schema: str, table: str) -> tuple[int, int]:
    """(min, max) of a table's `year`, or (None, None) if it has no year column."""
    if not _has_column(con, schema, table, "year"):
        return (None, None)
    return con.sql(f'select min(year), max(year) from "{schema}"."{table}"').fetchone()


def build_sources(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """Row counts, year span and load time for each dlt landing table.

    Freshness comes from `_dlt_load_id`, which dlt stamps as a **unix epoch in a
    varchar column** — the same value `dbt source freshness` reads. It records
    when *this pipeline* loaded the data, not when the publisher released it, so
    a stale timestamp here means the pipeline stopped running, not that OWID
    stopped publishing.
    """
    rows = []
    for table in SOURCE_TABLES:
        if not _has_column(con, "raw", table, "_dlt_load_id"):
            continue
        n, loaded_at = con.sql(
            f"""
            select
                count(*),
                to_timestamp(max(cast(_dlt_load_id as double)))
            from raw."{table}"
            """
        ).fetchone()
        year_min, year_max = _year_span(con, "raw", table)
        rows.append(
            {
                "source_table": f"raw.{table}",
                "rows": n,
                "year_min": year_min,
                "year_max": year_max,
                "loaded_at": loaded_at,
            }
        )
    return pl.DataFrame(rows).sort("source_table")


def build_tables(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """Row counts and year spans for every table in the modelled layers.

    Excludes this module's own output. Without that the inventory inventories
    itself, and the table count depends on whether it has run before — 10 on a
    first build, 13 on every later one, for no change in the warehouse.
    """
    listed = con.execute(
        """
        select table_schema, table_name
        from information_schema.tables
        where table_schema in (select unnest(?))
          and table_name not like 'pipeline\\_%' escape '\\'
        order by table_schema, table_name
        """,
        [list(LAYERS)],
    ).fetchall()

    rows = []
    for schema, table in listed:
        n = con.sql(f'select count(*) from "{schema}"."{table}"').fetchone()[0]
        year_min, year_max = _year_span(con, schema, table)
        rows.append(
            {
                "layer": schema,
                "table_name": f"{schema}.{table}",
                "rows": n,
                "year_min": year_min,
                "year_max": year_max,
            }
        )
    return pl.DataFrame(rows)


def _manifest_tests(manifest_path: str) -> dict[str, dict]:
    """Map audit-table name -> {test type, model it guards, column}.

    The audit table is named after the test's `alias`, and dbt **truncates and
    hashes** an alias longer than 63 characters
    (`dbt_utils_accepted_range_fct_c_1c6718ee2bb...`), so the table name on its
    own is not a readable label. The manifest is where the real name, the
    attached model and the tested column live.
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
            # `model.modern_data_stack.fct_emissions_energy` -> the last segment
            "tested_model": attached.rsplit(".", 1)[-1] or None,
            "tested_column": (metadata.get("kwargs") or {}).get("column_name"),
        }
    return out


def build_tests(con: duckdb.DuckDBPyConnection, manifest_path: str = MANIFEST_PATH) -> pl.DataFrame:
    """One row per dbt test, with the number of rows currently failing it.

    `dbt_project.yml` sets `+store_failures: true` project-wide, so each test
    leaves a table in `dbt_test__audit` holding the rows it rejected. An empty
    table is a passing test — which is why this counts rather than checks
    existence, and why a green pipeline produces a table of 91 zeroes.
    """
    audit_tables = [
        row[0]
        for row in con.sql(
            """
            select table_name from information_schema.tables
            where table_schema = 'dbt_test__audit'
            order by table_name
            """
        ).fetchall()
    ]
    catalogue = _manifest_tests(manifest_path)

    rows = []
    for table in audit_tables:
        failing = con.sql(f'select count(*) from dbt_test__audit."{table}"').fetchone()[0]
        meta = catalogue.get(table, {})
        rows.append(
            {
                "test_name": meta.get("test_name") or table,
                "test_type": meta.get("test_type"),
                "tested_model": meta.get("tested_model"),
                "tested_column": meta.get("tested_column"),
                "failing_rows": failing,
                "status": "fail" if failing else "pass",
                "audit_table": f"dbt_test__audit.{table}",
            }
        )
    return pl.DataFrame(rows)


def run(duckdb_path: str = DUCKDB_PATH, manifest_path: str = MANIFEST_PATH) -> dict[str, int]:
    """Write the three `analytics.pipeline_*` tables. Returns rows written each."""
    con = duckdb.connect(duckdb_path)
    try:
        frames = {
            "pipeline_sources": build_sources(con),
            "pipeline_tables": build_tables(con),
            "pipeline_tests": build_tests(con, manifest_path),
        }
        con.sql("create schema if not exists analytics")
        for name, frame in frames.items():
            con.register("frame_df", frame)  # DuckDB reads Polars frames directly
            con.sql(f"create or replace table analytics.{name} as select * from frame_df")
            con.unregister("frame_df")
        return {name: frame.height for name, frame in frames.items()}
    finally:
        con.close()


def main() -> None:
    for name, rows in run().items():
        print(f"wrote analytics.{name} ({rows} rows)")


if __name__ == "__main__":
    main()
