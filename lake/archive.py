"""Hive-partitioned Parquet archive of the warehouse — the lake half of the stack.

Everything else here lives *inside* one DuckDB file. This writes the year-keyed
tables back out as partitioned Parquet:

    data/lake/<table>/year=<year>/data_0.parquet

The mechanism, and why the lake is written *from* the warehouse rather than
landed into it, is in `modern_data_stack.lake`. What's here is this project's
table list and partition column.

Run:  uv run python -m lake.archive
"""

from __future__ import annotations

from modern_data_stack.lake import archive, table_dir

# Aliased: `run()` takes a `lake_dir` argument, and an unaliased import would be
# shadowed by it — a later `lake_dir()` inside the body would be a runtime
# `TypeError: 'str' object is not callable`, which no linter here would catch.
from modern_data_stack.paths import lake_dir as default_lake_dir, warehouse_path

DUCKDB_PATH = warehouse_path()

# LAKE_DIR mirrors WAREHOUSE_PATH: the tests point it at a temp directory.
LAKE_DIR = default_lake_dir()

# The year-keyed tables, raw and modelled. `raw` is here on purpose — the diff
# between two runs of a *landing* table is the interesting one, since that's
# where an upstream restatement first shows up. Tables without a `year` column
# (`raw.wb_country`, `staging.stg_country`) have nothing to partition on and are
# small enough not to want it.
#
# `marts.fct_eu_electricity_prices_semiannual` is deliberately absent: 1.4k rows
# over 19 years would add 19 partitions of ~4 kB to an archive whose small-file
# problem is already documented, and every byte of it is derivable from
# `raw.eu_elec_prices`, which is archived.
#
# The FX rates break the raw-first pattern, and it is a column that decides it:
# `raw.ecb_fx_rates` is keyed on `rate_date` and has no `year` to partition on,
# while `marts.fct_fx_rates_daily` carries one. So the modelled table is archived
# and the landing table is not — the reverse of the choice made for the four
# above. It is also the one table here that *improves* the small-file arithmetic:
# 381k rows over 28 partitions averages 13.6k rows each, against the archive's
# current ~47 kB.
ARCHIVED_TABLES = (
    "raw.owid_co2",
    "raw.owid_energy",
    "raw.wb_wdi",
    "raw.eu_elec_prices",
    "marts.fct_emissions_energy",
    "marts.fct_fx_rates_daily",
)

PARTITION_COLUMN = "year"

__all__ = ["ARCHIVED_TABLES", "LAKE_DIR", "PARTITION_COLUMN", "main", "run", "table_dir"]


def run(duckdb_path: str = DUCKDB_PATH, lake_dir: str = LAKE_DIR) -> dict[str, dict]:
    """Write every table in `ARCHIVED_TABLES` to `lake_dir`. Returns a per-table
    summary (used as asset metadata by the orchestrator)."""
    return archive(ARCHIVED_TABLES, duckdb_path, lake_dir, PARTITION_COLUMN)


def main() -> None:
    for table, stats in run().items():
        print(
            f"{table}: {stats['rows']:,} rows in {stats['partitions']} partitions "
            f"({stats['bytes'] / 1e6:.1f} MB)"
        )


if __name__ == "__main__":
    main()
