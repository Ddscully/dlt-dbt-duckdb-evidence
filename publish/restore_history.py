"""Carry this warehouse's unreproducible tables forward from a published release.

Run:  uv run python -m publish.restore_history <previous warehouse.duckdb>
      (or `just restore-history prev/warehouse.duckdb`)

Every workflow builds from an empty file, so without this the published
snapshots would hold one version per row forever. Copying the previous release's
tables in *before* the graph runs lets `dbt snapshot` compare this month's
numbers against last month's and append real revisions; `pages.yml` borrows the
newest release the same way. When `lakehouse.tar.gz` sits beside the source
file the landing zone comes too, so the weather archive deepens instead of
cold-starting.

Unreproducible for different reasons, carried by one command: a snapshot or a
run record in principle (no rebuild can invent a past revision or a finished
invocation), the weather archive within a budget (refetching it costs more than
Open-Meteo's daily allowance).

It refuses to overwrite. A destination table with rows stops the restore unless
`--force`; a landing zone with rows, or dlt local state, stops it outright (see
`lake.lakehouse.preflight`). `just clean warehouse` asks the same question
through `irreplaceable_rows()`, so the two cannot drift. Each source relation
must also carry the columns that make it what it claims to be — see `Carry`.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

from modern_data_stack.history import (
    SCD2_COLUMNS,
    Carry,
    carried_rows,
    restore,
)
from modern_data_stack.paths import warehouse_path

DUCKDB_PATH = warehouse_path()

HISTORY_SCHEMA = "history"
RAW_SCHEMA = "raw"
ANALYTICS_SCHEMA = "analytics"

# The run-history table and the two columns that prove a relation is one. Both
# are needed: `invocation_id` alone would match anything keyed on a run, and
# `execution_time_s` is what makes it a *timing* record rather than a log.
RUNS_TABLE = "pipeline_runs"
RUN_COLUMNS = ("invocation_id", "execution_time_s")

# The published landing zone, as it is named in the release.
# `publish/export_warehouse.LAKEHOUSE_ASSET` is the other half; a test holds them
# together, because a rename here would make the restore silently find nothing
# and cold-start the weather archive with nothing going red.
LAKEHOUSE_ASSET = "lakehouse.tar.gz"

# What this warehouse cannot rebuild, and the columns that prove each relation is
# what it claims to be (see `Carry`). `history` is carried whole, since everything
# in it is a snapshot, so a new snapshot needs no edit here. The landing zone is
# not a rule: it lives outside the DuckDB file, and `_restore_lakehouse` copies it.
CARRIED: tuple[Carry, ...] = (
    Carry(schema=HISTORY_SCHEMA, kind="dbt snapshot", required_columns=SCD2_COLUMNS),
    # The dbt run history: the invocation is over and `run_results.json` holds
    # only the latest. Named rather than the whole schema, because the other
    # `analytics` tables are rebuilt every run and carrying them would restore
    # last month's.
    Carry(
        schema=ANALYTICS_SCHEMA,
        kind="dbt run history",
        required_columns=RUN_COLUMNS,
        tables=(RUNS_TABLE,),
    ),
)

__all__ = [
    "ANALYTICS_SCHEMA",
    "CARRIED",
    "DUCKDB_PATH",
    "HISTORY_SCHEMA",
    "LAKEHOUSE_ASSET",
    "RAW_SCHEMA",
    "RUNS_TABLE",
    "RUN_COLUMNS",
    "irreplaceable_rows",
    "main",
    "run",
]


def run(
    source: str | Path,
    duckdb_path: str | Path = DUCKDB_PATH,
    force: bool = False,
    lakehouse_dir: str | Path | None = None,
) -> dict:
    """Copy `source`'s unreproducible tables into `duckdb_path`. Returns a summary.

    Restoring nothing is normal — the first release has no predecessor. The
    landing zone's refusals (`lakehouse.preflight`) are asked before anything is
    written, so a refusal cannot leave `history` already replaced.
    """
    src = Path(source)
    archive = src.parent / LAKEHOUSE_ASSET
    if archive.exists():
        # Only with a landing zone to restore: a history-only restore does not
        # touch what dlt's local state describes, so it must not be refused.
        from lake import lakehouse

        lakehouse.preflight(lakehouse.LAKEHOUSE_DIR if lakehouse_dir is None else lakehouse_dir)

    summary = restore(src, duckdb_path, carry=CARRIED, force=force)
    summary["lakehouse"] = _restore_lakehouse(src, lakehouse_dir)
    return summary


def _restore_lakehouse(source: Path, lakehouse_dir: str | Path | None) -> dict[str, int]:
    """Carry the published landing zone in, if the release has one beside `source`.

    Found by position rather than a flag, because the export's layout fixes it.
    A release without one is the normal nothing-to-restore case.
    `lakehouse.restore` repeats `run()`'s preflight, as a public entry point must.
    """
    import tarfile
    import tempfile

    from lake import lakehouse

    archive = source.parent / LAKEHOUSE_ASSET
    if not archive.exists():
        return {}
    target = lakehouse_dir if lakehouse_dir is not None else lakehouse.LAKEHOUSE_DIR
    with tempfile.TemporaryDirectory() as staging:
        with tarfile.open(archive) as tar:
            tar.extractall(staging, filter="data")
        return lakehouse.restore(Path(staging) / "lakehouse", target)


def irreplaceable_rows(duckdb_path: str | Path = DUCKDB_PATH) -> int:
    """Rows in this warehouse that no rebuild could make again.

    Asked by `just clean warehouse` before deleting the file and by
    `release-data.yml` on both sides of the build — one function, so a rule added
    to `CARRIED` reaches all three. The landing zone's counterpart is
    `lake.lakehouse.carried_rows`.
    """
    path = Path(duckdb_path)
    if not path.exists():
        return 0
    con = duckdb.connect(str(path), read_only=True)
    try:
        return sum(carried_rows(con, CARRIED).values())
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", help="the previous release's warehouse.duckdb")
    parser.add_argument("--warehouse", default=DUCKDB_PATH, help="destination DuckDB file")
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace destination tables even when they already hold rows",
    )
    args = parser.parse_args()

    summary = run(args.source, args.warehouse, args.force)
    for table, rows in summary.get("lakehouse", {}).items():
        print(f"  {table:32} {rows:>8,} rows  (lakehouse)")
    if not summary["tables"]:
        print(f"nothing to carry forward from {summary['source']} — starting from empty")
        return
    print(f"restored into {summary['destination']} from {summary['source']}:")
    for table in summary["tables"]:
        print(f"  {table['table']:32} {table['rows']:>8,} rows")


if __name__ == "__main__":
    main()
