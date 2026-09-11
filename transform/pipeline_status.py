"""Pipeline observability: turn the warehouse's own metadata into queryable tables.

Writes four flat tables into `analytics`:

* `pipeline_sources` — one row per dlt landing table: rows, span, when it loaded.
* `pipeline_tables`  — one row per table in every modelled layer: rows, year span.
* `pipeline_tests`   — one row per dbt test: what it guards and how many rows
  are currently failing it.
* `pipeline_runs`    — one row per node per dbt invocation: what ran, and how
  long it took. The only one that accumulates.

`reports/pages/pipeline.md` renders them. The first three are replaced each run;
`pipeline_runs` is appended, because each invocation's artifact is overwritten by
the next — so no rebuild can reproduce it, and `publish/restore_history.CARRIED`
carries it between releases.

The queries live in `modern_data_stack.observability`; this module holds the
project's landing tables and layer names. Run it after `dbt build`, whose audit
schema and artifacts it reads.

Run:  uv run python -m transform.pipeline_status
"""

from __future__ import annotations

import duckdb
import polars as pl

from lake.lakehouse import ATTACH_ALIAS, LAKEHOUSE_DIR, catalog_path, data_path
from modern_data_stack import db, observability
from modern_data_stack.ducklake import attach
from modern_data_stack.paths import dbt_manifest_path, dbt_run_results_path, warehouse_path

DUCKDB_PATH = warehouse_path()

# Both in the gitignored `dbt/target/` and both optional: without the manifest
# the test inventory falls back to bare audit-table names.
MANIFEST_PATH = dbt_manifest_path()
RUN_RESULTS_PATH = dbt_run_results_path()

# Appended rather than replaced — see the module docstring.
RUNS_TABLE = "pipeline_runs"

# The modelled schemas, in pipeline order. `raw` is `build_sources`' (it has
# load times); dbt's bookkeeping schemas are left out.
LAYERS = ("staging", "intermediate", "marts", "analytics", "history")

# dlt's landing tables, without its `_dlt_*` bookkeeping. Those with no `year`
# column (the country dimension; FX, retail and weather, which are date-keyed)
# report a null span.
SOURCE_TABLES = (
    "owid_co2",
    "owid_energy",
    "wb_country",
    "wb_wdi",
    "eu_elec_prices",
    "ecb_fx_rates",
    "retail_invoice_lines",
    "om_weather_daily",
)


def build_sources(
    con: duckdb.DuckDBPyConnection, raw_database: str | None = ATTACH_ALIAS
) -> pl.DataFrame:
    """Row counts, year span and load time for each dlt landing table.

    Reads `raw` from the lakehouse, which `con` must have attached (`run()`
    does). The catalog is named explicitly because `information_schema` spans
    every attached database.
    """
    return observability.build_sources(con, SOURCE_TABLES, raw_database=raw_database)


def build_tables(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """Row counts and year spans for every table in the modelled layers."""
    return observability.build_tables(con, LAYERS)


def build_tests(con: duckdb.DuckDBPyConnection, manifest_path: str = MANIFEST_PATH) -> pl.DataFrame:
    """One row per dbt test, with the number of rows currently failing it."""
    return observability.build_tests(con, manifest_path)


def build_runs(
    manifest_path: str = MANIFEST_PATH, run_results_path: str = RUN_RESULTS_PATH
) -> pl.DataFrame:
    """One row per node in the last dbt invocation, with its timings.

    Reads only dbt's files, so it takes no connection.
    """
    return observability.build_runs(run_results_path, observability.manifest_nodes(manifest_path))


def run(
    duckdb_path: str = DUCKDB_PATH,
    manifest_path: str = MANIFEST_PATH,
    lakehouse_dir: str = LAKEHOUSE_DIR,
    run_results_path: str = RUN_RESULTS_PATH,
) -> dict[str, int]:
    """Write the four `analytics.pipeline_*` tables. Returns rows written each.

    For `pipeline_runs` the count is rows added — 0 when this invocation was
    already recorded.
    """
    con = duckdb.connect(duckdb_path)
    try:
        # `raw` lives in the lakehouse.
        attach(
            con,
            catalog_path(lakehouse_dir),
            data_path(lakehouse_dir),
            ATTACH_ALIAS,
            read_only=True,
        )
        frames = {
            "pipeline_sources": build_sources(con),
            "pipeline_tables": build_tables(con),
            "pipeline_tests": build_tests(con, manifest_path),
        }
        written = db.write_frames(con, frames, "analytics")
        written[RUNS_TABLE] = db.append_frame(
            con,
            build_runs(manifest_path, run_results_path),
            "analytics",
            RUNS_TABLE,
            key="invocation_id",
        )
        return written
    finally:
        con.close()


def main() -> None:
    for name, rows in run().items():
        print(f"wrote analytics.{name} ({rows} rows)")


if __name__ == "__main__":
    main()
