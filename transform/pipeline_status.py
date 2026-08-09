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

from modern_data_stack import observability
from modern_data_stack.paths import dbt_manifest_path, warehouse_path

DUCKDB_PATH = warehouse_path()

# dbt writes the manifest into the gitignored `dbt/target/`, so this is only
# present after a `dbt build`/`dbt parse`. Absent, the test inventory falls back
# to whatever audit tables exist — see `observability.manifest_tests`.
MANIFEST_PATH = dbt_manifest_path()

# The schemas that make up the modelled warehouse, in pipeline order. `raw` is
# covered separately by `build_sources` (it has freshness, these don't) and
# dbt's own bookkeeping schemas are deliberately absent.
LAYERS = ("staging", "marts", "analytics", "history")

# dlt's landing tables, minus its internal `_dlt_*` bookkeeping.
SOURCE_TABLES = ("owid_co2", "owid_energy", "wb_country", "wb_wdi", "eu_elec_prices")


def build_sources(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """Row counts, year span and load time for each dlt landing table."""
    return observability.build_sources(con, SOURCE_TABLES)


def build_tables(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """Row counts and year spans for every table in the modelled layers."""
    return observability.build_tables(con, LAYERS)


def build_tests(con: duckdb.DuckDBPyConnection, manifest_path: str = MANIFEST_PATH) -> pl.DataFrame:
    """One row per dbt test, with the number of rows currently failing it."""
    return observability.build_tests(con, manifest_path)


def run(duckdb_path: str = DUCKDB_PATH, manifest_path: str = MANIFEST_PATH) -> dict[str, int]:
    """Write the three `analytics.pipeline_*` tables. Returns rows written each."""
    con = duckdb.connect(duckdb_path)
    try:
        frames = {
            "pipeline_sources": build_sources(con),
            "pipeline_tables": build_tables(con),
            "pipeline_tests": build_tests(con, manifest_path),
        }
        return observability.write_status(con, frames)
    finally:
        con.close()


def main() -> None:
    for name, rows in run().items():
        print(f"wrote analytics.{name} ({rows} rows)")


if __name__ == "__main__":
    main()
