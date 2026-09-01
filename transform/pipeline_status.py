"""Pipeline observability: turn the warehouse's own metadata into queryable tables.

Writes three flat tables into `analytics`:

* `pipeline_sources` — one row per dlt landing table: rows, span, when it loaded.
* `pipeline_tables`  — one row per table in every modelled layer: rows, year span.
* `pipeline_tests`   — one row per dbt test: what it guards and how many rows
  are currently failing it.

`reports/pages/pipeline.md` renders the three. They are a *snapshot* written at
run time, not a history — nothing here accumulates across runs the way
`history.snap_co2_estimates` does.

The queries live in `modern_data_stack.observability`; what's here is this
project's landing tables and layer names. It must run **after** `dbt build`: it
reads `dbt_test__audit` and `dbt/target/manifest.json`, neither of which exists
before one.

Run:  uv run python -m transform.pipeline_status
"""

from __future__ import annotations

import duckdb
import polars as pl

from lake.lakehouse import ATTACH_ALIAS, LAKEHOUSE_DIR, catalog_path, data_path
from modern_data_stack import db, observability
from modern_data_stack.ducklake import attach
from modern_data_stack.paths import dbt_manifest_path, warehouse_path

DUCKDB_PATH = warehouse_path()

# dbt writes the manifest into the gitignored `dbt/target/`, so this is only
# present after a `dbt build`/`dbt parse`. Absent, the test inventory falls back
# to whatever audit tables exist — see `observability.manifest_tests`.
MANIFEST_PATH = dbt_manifest_path()

# The schemas that make up the modelled warehouse, in pipeline order. `raw` is
# covered separately by `build_sources` (it has freshness, these don't) and
# dbt's own bookkeeping schemas are deliberately absent.
LAYERS = ("staging", "intermediate", "marts", "analytics", "history")

# dlt's landing tables, minus its internal `_dlt_*` bookkeeping.
#
# `ecb_fx_rates` and `retail_invoice_lines` report a null year span, because both
# are keyed on a date rather than a year and neither has a `year` column to take
# one from. That is the same shape `stg_country` and
# the currency dimension already have in `pipeline_tables`, and it is left as a
# null rather than derived: the row's job here is the row count and the load
# time, which is the freshness half of the page.
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

    Reads the **lakehouse**, not the warehouse: dlt lands in the DuckLake catalog
    and the DuckDB file holds only what dbt builds. `con` must therefore have the
    catalog attached — `run()` below does it, and naming the database explicitly
    is not decoration. `information_schema` spans every attached catalog, so a
    `raw` schema in either one would match a query that filtered on the schema
    alone, and this project now genuinely has two catalogs open at once.
    """
    return observability.build_sources(con, SOURCE_TABLES, raw_database=raw_database)


def build_tables(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """Row counts and year spans for every table in the modelled layers."""
    return observability.build_tables(con, LAYERS)


def build_tests(con: duckdb.DuckDBPyConnection, manifest_path: str = MANIFEST_PATH) -> pl.DataFrame:
    """One row per dbt test, with the number of rows currently failing it."""
    return observability.build_tests(con, manifest_path)


def run(
    duckdb_path: str = DUCKDB_PATH,
    manifest_path: str = MANIFEST_PATH,
    lakehouse_dir: str = LAKEHOUSE_DIR,
) -> dict[str, int]:
    """Write the three `analytics.pipeline_*` tables. Returns rows written each."""
    con = duckdb.connect(duckdb_path)
    try:
        # `raw` is in the lakehouse, so the inventory cannot be built without
        # it attached — the three tables describe one pipeline across two
        # catalogs now.
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
        return db.write_frames(con, frames, "analytics")
    finally:
        con.close()


def main() -> None:
    for name, rows in run().items():
        print(f"wrote analytics.{name} ({rows} rows)")


if __name__ == "__main__":
    main()
