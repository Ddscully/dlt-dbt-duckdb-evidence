"""Pipeline observability: turn the warehouse's own metadata into queryable tables.

Writes four flat tables into `analytics`:

* `pipeline_sources` — one row per dlt landing table: rows, span, when it loaded.
* `pipeline_tables`  — one row per table in every modelled layer: rows, year span.
* `pipeline_tests`   — one row per dbt test: what it guards and how many rows
  are currently failing it.
* `pipeline_runs`    — one row per node per dbt invocation: what ran, and how
  long it took. The only one that accumulates.

`reports/pages/pipeline.md` renders them. The first three are a *snapshot*
written at run time; `pipeline_runs` is the exception and is a history, because
the invocation it describes is over and its artifact is overwritten by the next
one. That makes it the third thing in this warehouse no rebuild can reproduce,
alongside the dbt snapshots and the weather archive, and it is carried between
releases by the same machinery — `publish/restore_history.CARRIED`.

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
from modern_data_stack.paths import dbt_manifest_path, dbt_run_results_path, warehouse_path

DUCKDB_PATH = warehouse_path()

# dbt writes the manifest into the gitignored `dbt/target/`, so this is only
# present after a `dbt build`/`dbt parse`. Absent, the test inventory falls back
# to whatever audit tables exist — see `observability.manifest_tests`.
MANIFEST_PATH = dbt_manifest_path()

# dbt's per-node timings for the last invocation that executed anything. Same
# gitignored directory as the manifest and the same tolerance for absence, but
# it is read for a different purpose: the three tables below describe the
# warehouse *now*, and this one accumulates what each build cost.
RUN_RESULTS_PATH = dbt_run_results_path()

# The one table here that is appended rather than replaced, and so the one this
# project cannot rebuild. `publish/restore_history.CARRIED` carries it between
# releases for that reason — see the rule there.
RUNS_TABLE = "pipeline_runs"

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


def build_runs(
    manifest_path: str = MANIFEST_PATH, run_results_path: str = RUN_RESULTS_PATH
) -> pl.DataFrame:
    """One row per node in the last dbt invocation, with its timings.

    Reads no database at all — both inputs are files dbt wrote — which is why it
    takes no connection where the other three builders do.
    """
    return observability.build_runs(run_results_path, observability.manifest_nodes(manifest_path))


def run(
    duckdb_path: str = DUCKDB_PATH,
    manifest_path: str = MANIFEST_PATH,
    lakehouse_dir: str = LAKEHOUSE_DIR,
    run_results_path: str = RUN_RESULTS_PATH,
) -> dict[str, int]:
    """Write the four `analytics.pipeline_*` tables. Returns rows written each.

    Three are replaced and `pipeline_runs` is appended; the returned count for it
    is rows *added*, which is 0 when the artifact has already been recorded and
    is the honest answer rather than the table's height.
    """
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
