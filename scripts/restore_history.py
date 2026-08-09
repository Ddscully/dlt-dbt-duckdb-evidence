"""Carry the SCD2 snapshot history forward from a previously published release.

Run:  uv run python -m scripts.restore_history <previous warehouse.duckdb>
      (or `just restore-history prev/warehouse.duckdb`)

Every workflow builds from an empty file, so `history.snap_co2_estimates` held
one version per row and the Restatements page shipped permanently in its
"nothing revised yet" branch — the one table here that a rebuild cannot
reproduce was the one table the published copy never had. This script is the
other half: it copies the `history` schema out of the previous release into the
fresh warehouse *before* the graph runs, so `dbt snapshot` compares this month's
numbers against last month's and appends a version where OWID has restated.
Monthly releases then accumulate a real revision log, and `pages.yml` borrows the
newest one so the published page shows it.

## Why before the build, and why it is safe to pre-create the file

dbt appends to the snapshot during `dbt build`, so the previous rows have to be
on disk before that — which means writing into `data/warehouse.duckdb` before dlt
has created it. That is fine: dlt keys "is this destination fresh?" on its own
bookkeeping inside the `raw` dataset, which a `history`-only file does not have,
so it still performs a full load (and still resets the WDI watermark). The
restore never touches `raw`, `staging`, `marts` or `analytics`.

## What it refuses to do

Overwrite history that is already there. `rm data/warehouse.duckdb` is the only
destructive act in this repo and this script would be the second one; a
destination table with rows in it stops the restore unless `--force`. It also
checks the source table carries dbt's SCD2 bookkeeping columns, because restoring
something snapshot-shaped-but-not-a-snapshot fails later, inside dbt, with a much
worse error message.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from modern_data_stack.history import SCD2_COLUMNS, restore
from modern_data_stack.paths import warehouse_path

DUCKDB_PATH = warehouse_path()

HISTORY_SCHEMA = "history"

__all__ = ["DUCKDB_PATH", "HISTORY_SCHEMA", "SCD2_COLUMNS", "main", "run"]


def run(
    source: str | Path,
    duckdb_path: str | Path = DUCKDB_PATH,
    force: bool = False,
) -> dict:
    """Copy `source`'s `history` schema into `duckdb_path`. Returns a summary.

    Restoring nothing is a normal outcome — the first release ever cut has no
    predecessor, and a source with no `history` schema (or an empty one) is the
    same case. Only a *destination* that already has history is an error.
    """
    return restore(source, duckdb_path, force=force, history_schema=HISTORY_SCHEMA)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", help="the previous release's warehouse.duckdb")
    parser.add_argument("--warehouse", default=DUCKDB_PATH, help="destination DuckDB file")
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace destination history even when it already holds rows",
    )
    args = parser.parse_args()

    summary = run(args.source, args.warehouse, args.force)
    if not summary["tables"]:
        print(f"no snapshot history in {summary['source']} — starting from empty")
        return
    print(f"restored into {summary['destination']} from {summary['source']}:")
    for table in summary["tables"]:
        print(f"  {table['table']:32} {table['rows']:>8,} rows")


if __name__ == "__main__":
    main()
