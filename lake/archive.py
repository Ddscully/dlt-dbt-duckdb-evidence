"""Hive-partitioned Parquet archive of the warehouse — the lake half of the stack.

Everything else here lives *inside* one DuckDB file. This writes the year-keyed
tables back out as partitioned Parquet:

    data/lake/<table>/year=<year>/data_0.parquet

which buys three things the single file doesn't:

* **Partition pruning.** `where year = 2020` reads one file, not the table.
* **Portability.** Any engine reads Parquet; a DuckDB file is a DuckDB version.
* **Diffability.** Re-run the pipeline and only the partitions whose data moved
  have different bytes, so `sha256sum` tells you what OWID changed. The DuckDB
  file differs everywhere, every time.

It is written *from* the warehouse rather than being ingested *into* it. dlt's
filesystem destination can write Parquet but not partition it by a data column,
and DuckDB's `COPY … PARTITION_BY` can — so the honest arrangement is an archive
of the warehouse, not a landing zone in front of it.

Run:  uv run python -m lake.archive
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import duckdb

from ingest.pipeline import DUCKDB_PATH, REPO_ROOT

# LAKE_DIR mirrors WAREHOUSE_PATH: the tests point it at a temp directory.
LAKE_DIR = os.environ.get("LAKE_DIR") or str(REPO_ROOT / "data" / "lake")

# The year-keyed tables, raw and modelled. `raw` is here on purpose — the diff
# between two runs of a *landing* table is the interesting one, since that's
# where an upstream restatement first shows up. Tables without a `year` column
# (`raw.wb_country`, `staging.stg_country`) have nothing to partition on and are
# small enough not to want it.
ARCHIVED_TABLES = (
    "raw.owid_co2",
    "raw.owid_energy",
    "raw.wb_wdi",
    "raw.eu_elec_prices",
    "marts.fct_emissions_energy",
)

PARTITION_COLUMN = "year"


def table_dir(lake_dir: str | Path, table: str) -> Path:
    """`raw.owid_co2` → `<lake>/raw_owid_co2`. Flat, because a dot in a path
    component reads as a file extension to half the tools that will see it."""
    return Path(lake_dir) / table.replace(".", "_")


def _summarise(con: duckdb.DuckDBPyConnection, target: Path) -> dict:
    """Row count, partition count and size of what was just written, read back
    through `read_parquet` — so the summary is evidence the files are readable,
    not just that `COPY` returned."""
    glob = f"{target}/**/*.parquet"
    rows, partitions = con.sql(
        f"""
        select count(*), count(distinct {PARTITION_COLUMN})
        from read_parquet('{glob}', hive_partitioning = 1)
        """
    ).fetchone()
    files = sorted(target.glob(f"{PARTITION_COLUMN}=*/*.parquet"))
    return {
        "rows": rows,
        "partitions": partitions,
        "files": len(files),
        "bytes": sum(f.stat().st_size for f in files),
    }


def run(duckdb_path: str = DUCKDB_PATH, lake_dir: str = LAKE_DIR) -> dict[str, dict]:
    """Write every table in `ARCHIVED_TABLES` to `lake_dir`. Returns a per-table
    summary (used as asset metadata by the orchestrator)."""
    Path(lake_dir).mkdir(parents=True, exist_ok=True)
    # Read-only: the archive never writes to the warehouse, so it can run beside
    # a reader without taking the single writer lock.
    con = duckdb.connect(duckdb_path, read_only=True)
    try:
        summary = {}
        for table in ARCHIVED_TABLES:
            target = table_dir(lake_dir, table)
            # Clear the directory first. DuckDB's `overwrite` only replaces the
            # partitions it is *writing*, so a year that lost its last row
            # upstream would keep answering queries out of a stale file — the
            # archive would disagree with the warehouse and the parity check
            # would be the only thing to notice. Rewriting from empty is cheap
            # (seconds) and the whole thing is regenerable anyway.
            if target.exists():
                shutil.rmtree(target)
            con.sql(
                f"""
                copy (select * from {table})
                to '{target}'
                (format parquet, partition_by ({PARTITION_COLUMN}), compression zstd)
                """
            )
            summary[table] = _summarise(con, target)
        return summary
    finally:
        con.close()


def main() -> None:
    for table, stats in run().items():
        print(
            f"{table}: {stats['rows']:,} rows in {stats['partitions']} partitions "
            f"({stats['bytes'] / 1e6:.1f} MB)"
        )


if __name__ == "__main__":
    main()
