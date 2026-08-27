"""DuckLake mirror of the weather tables — the lakehouse half of the file layer.

`lake/archive.py` writes hive-partitioned Parquet by hand. This writes the same
kind of files through DuckLake, which adds a catalog database holding schema,
snapshots and per-file statistics. The mechanism is in
`modern_data_stack.ducklake`; what is here is this project's table list and
merge keys.

Run:  uv run python -m lake.lakehouse

## Why weather, and why not the tables the archive already holds

The format's whole argument is that it can tell you *what changed*. Nothing else
in this warehouse exercises that:

* `marts.fct_fx_rates_daily` grows by ~29 rows a business day and never restates;
* `marts.fct_retail_order_line` is frozen at 2011-12 and cannot drift at all;
* `raw.owid_co2` restates in principle and has produced **zero** observed
  revisions locally — `history.snap_co2_estimates` holds no superseded row.

`raw.om_weather_daily` is the one that does. Open-Meteo serves preliminary ERA5T
within a day or two of real time and Copernicus supersedes it with final ERA5
two to three months later, so `WEATHER_LOOKBACK_DAYS` re-asks for the last 90
days on every run and 41 x 90 = 3,690 rows are re-merged in place at daily
grain. Those are real revisions on a *scheduled* upstream property, not an
occasional event and not the simulated one the snapshot documentation falls back
on.

Weather is also the cleanest place in the repo to introduce the format, because
it is not in the hive archive at all — `ARCHIVED_TABLES` lists seven tables and
neither weather table is among them. There is no existing layout to migrate, so
nothing here is a swap. The two layers sit beside each other and the archive is
untouched.

**And this table is the sharpest available case for the format's headline
claim.** `raw.om_weather_daily` has no `year` column — it is keyed on
`weather_date` — against the archive's `PARTITION_COLUMN = "year"`. Putting it
in the hive lake would need a derived column invented for the directory layout.
DuckLake needs none: it prunes on catalog statistics, so the partition column
that the archive must choose up front is one this layer never chooses at all.
"""

from __future__ import annotations

from pathlib import Path

from modern_data_stack.ducklake import Synced, sync
from modern_data_stack.paths import lakehouse_dir as default_lakehouse_dir, warehouse_path

DUCKDB_PATH = warehouse_path()

# LAKEHOUSE_DIR mirrors LAKE_DIR and WAREHOUSE_PATH: the tests point it at a
# temp directory so a fixture run cannot write over the real catalog.
LAKEHOUSE_DIR = default_lakehouse_dir()

# The catalog is a DuckDB file beside the data rather than inside it, so that
# `data/` holds nothing but Parquet — the property that lets somebody read the
# files without the catalog, which is the archive's whole appeal and worth not
# giving up for free.
CATALOG_NAME = "catalog.ducklake"
DATA_DIRNAME = "data"

# Every change goes to Parquet; the catalog holds metadata and nothing else.
#
# DuckLake's default is 10 — a change of ten rows or fewer is written into the
# catalog *database* instead of out to files. Neither value makes the directory
# readable on its own (see `modern_data_stack.ducklake`: the default hides small
# changes from `read_parquet`, and 0 exposes delete files it cannot apply), so
# the choice is made on two other grounds.
#
# **It keeps the fixture path and the real path the same shape.** A routine
# ingest restates 41 x 90 = 3,690 rows and would never inline; a test that
# restates one row would inline on the default and so exercise a code path
# production never takes. That is the class of difference this repo has been
# bitten by before — the fixture slice passing a bound the full data breaks.
#
# **And it keeps data out of the catalog.** The catalog is already the one piece
# of state here no rebuild reproduces; letting it also hold rows would widen
# what "carry the catalog forward" has to mean.
DATA_INLINING_ROW_LIMIT = 0

# The landing table and the mart above it, mirrored at both grains on purpose. A
# revised day restates one daily row and the country-year total that contains
# it, so the change feed tells the same story at two resolutions — which is the
# thing an archive of one layer cannot show.
#
# `_dlt_load_id` and `_dlt_id` are named as provenance because dlt regenerates
# both on every row it re-merges, byte-identical data or not. Comparing them
# would report all 3,690 rows of the merge window as revised on every single
# ingest, which is the exact opposite of what this layer is for. The mart has no
# such columns, which is why the field defaults to empty rather than to dlt's
# pair — a general module has no business assuming dlt.
SYNCED_TABLES = (
    Synced(
        table="raw.om_weather_daily",
        key=("country_iso3", "weather_date"),
        provenance_columns=("_dlt_load_id", "_dlt_id"),
    ),
    Synced(table="marts.fct_country_weather_year", key=("country_iso3", "year")),
)

__all__ = [
    "CATALOG_NAME",
    "DATA_DIRNAME",
    "DATA_INLINING_ROW_LIMIT",
    "DUCKDB_PATH",
    "LAKEHOUSE_DIR",
    "SYNCED_TABLES",
    "catalog_path",
    "data_path",
    "main",
    "run",
]


def catalog_path(lakehouse_dir: str | Path = LAKEHOUSE_DIR) -> Path:
    """The DuckLake catalog database — schema, snapshots and file statistics."""
    return Path(lakehouse_dir) / CATALOG_NAME


def data_path(lakehouse_dir: str | Path = LAKEHOUSE_DIR) -> Path:
    """Where DuckLake writes the Parquet itself. Plain files, no catalog needed."""
    return Path(lakehouse_dir) / DATA_DIRNAME


def run(duckdb_path: str = DUCKDB_PATH, lakehouse_dir: str = LAKEHOUSE_DIR) -> dict[str, dict]:
    """Merge every table in `SYNCED_TABLES` into the lakehouse. Per-table summary.

    A table nothing changed in comes back with `snapshot: None` and three zeroes,
    having written nothing at all — see `modern_data_stack.ducklake`.
    """
    return sync(
        SYNCED_TABLES,
        duckdb_path=duckdb_path,
        catalog_path=catalog_path(lakehouse_dir),
        data_path=data_path(lakehouse_dir),
        data_inlining_row_limit=DATA_INLINING_ROW_LIMIT,
    )


def main() -> None:
    for table, stats in run().items():
        if stats["snapshot"] is None:
            print(f"{table}: {stats['rows']:,} rows, unchanged")
            continue
        print(
            f"{table}: {stats['rows']:,} rows "
            f"(+{stats['inserted']:,} ~{stats['updated']:,} -{stats['deleted']:,}) "
            f"in snapshot {stats['snapshot']}, "
            f"{stats['files']} files / {stats['bytes'] / 1e6:.1f} MB"
        )


if __name__ == "__main__":
    main()
