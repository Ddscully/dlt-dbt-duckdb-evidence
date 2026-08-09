"""Write warehouse tables out as hive-partitioned Parquet.

The mechanism behind `lake/archive.py`. It buys three things a single database
file doesn't: partition pruning, portability to any engine that reads Parquet,
and a **diffable** artifact — re-run the pipeline and only the partitions whose
data actually moved have different bytes, so `sha256sum` tells you what upstream
changed. A DuckDB file differs everywhere on every run and can't answer that.

It writes the lake *from* the warehouse rather than landing into it. dlt's
filesystem destination writes Parquet but can't partition it by a data column,
and DuckDB's `COPY … PARTITION_BY` can — so the honest arrangement is an archive
of the warehouse, not a landing zone in front of it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import duckdb


def table_dir(lake_dir: str | Path, table: str) -> Path:
    """`raw.owid_co2` → `<lake>/raw_owid_co2`. Flat, because a dot in a path
    component reads as a file extension to half the tools that will see it."""
    return Path(lake_dir) / table.replace(".", "_")


def summarise(con: duckdb.DuckDBPyConnection, target: Path, partition_column: str = "year") -> dict:
    """Row count, partition count and size of what was just written, read back
    through `read_parquet` — so the summary is evidence the files are readable,
    not just that `COPY` returned."""
    glob = f"{target}/**/*.parquet"
    rows, partitions = con.sql(
        f"""
        select count(*), count(distinct {partition_column})
        from read_parquet('{glob}', hive_partitioning = 1)
        """
    ).fetchone()
    files = sorted(target.glob(f"{partition_column}=*/*.parquet"))
    return {
        "rows": rows,
        "partitions": partitions,
        "files": len(files),
        "bytes": sum(f.stat().st_size for f in files),
    }


def archive(
    tables: tuple[str, ...],
    duckdb_path: str,
    lake_dir: str,
    partition_column: str = "year",
) -> dict[str, dict]:
    """Write each qualified table to `lake_dir`, partitioned by `partition_column`.

    Returns a per-table summary, which the orchestration layer uses as asset
    metadata. Every table named has to carry the partition column.
    """
    Path(lake_dir).mkdir(parents=True, exist_ok=True)
    # Read-only: the archive never writes to the warehouse, so it can run beside
    # a reader without taking the single writer lock.
    con = duckdb.connect(duckdb_path, read_only=True)
    try:
        summary = {}
        for table in tables:
            target = table_dir(lake_dir, table)
            # Clear the directory first. DuckDB's `overwrite` only replaces the
            # partitions it is *writing*, so a partition that lost its last row
            # upstream would keep answering queries out of a stale file — the
            # archive would disagree with the warehouse and a parity check would
            # be the only thing to notice. Rewriting from empty is cheap and the
            # whole thing is regenerable anyway.
            if target.exists():
                shutil.rmtree(target)
            con.sql(
                f"""
                copy (select * from {table})
                to '{target}'
                (format parquet, partition_by ({partition_column}), compression zstd)
                """
            )
            summary[table] = summarise(con, target, partition_column)
        return summary
    finally:
        con.close()
