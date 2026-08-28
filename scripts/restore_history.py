"""Carry this warehouse's unreproducible tables forward from a published release.

Run:  uv run python -m scripts.restore_history <previous warehouse.duckdb>
      (or `just restore-history prev/warehouse.duckdb`)

Every workflow builds from an empty file, so `history.snap_co2_estimates` held
one version per row and the Restatements page shipped permanently in its
"nothing revised yet" branch — the tables here that a rebuild cannot reproduce
were the tables the published copy never had. This script is the other half: it
copies them out of the previous release into the fresh warehouse *before* the
graph runs, so `dbt snapshot` compares this month's numbers against last month's
and appends a version where OWID has restated. Monthly releases then accumulate a
real revision log, and `pages.yml` borrows the newest one so the published page
shows it.

**There are two reasons a table lands here and they are not the same reason.**
A dbt snapshot is state *in principle* — no rebuild can invent a revision.
`raw.om_weather_daily` is unreproducible within a *budget*: Open-Meteo's free
tier allows 10,000 weighted units a day and the archive costs more than that, so
the data exists upstream and simply cannot be re-fetched on every run. The
consequence is identical, which is why one mechanism covers both.

## Why before the build, and why it is safe to pre-create the file

dbt appends to the snapshot during `dbt build`, so the previous rows have to be
on disk before that — which means writing into `data/warehouse.duckdb` before dlt
has created it. That is fine: dlt keys "is this destination fresh?" on its own
bookkeeping tables, which a carried *data* table is not, so it still performs a
full load (and still resets the WDI watermark). Verified rather than assumed: a
fixture load into a restored file still fetched the full WDI series.

## What it refuses to do

Overwrite what is already there. Deleting `data/warehouse.duckdb` is the only act
in this repo that destroys something a rebuild cannot make again, and this script
would be the second one; a destination table with rows in it stops the restore
unless `--force`. `just clean warehouse` gates the deletion itself on the same
question, with the same answer, and asks it through `irreplaceable_rows()` below
so the two cannot drift. It also checks each source relation carries the
bookkeeping columns that make it what it claims to be — see `Carry`.
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

# The published landing zone, as it is named in the release.
# `scripts/export_warehouse.LAKEHOUSE_ASSET` is the other half; a test holds them
# together, because a rename here would make the restore silently find nothing
# and cold-start the weather archive with nothing going red.
LAKEHOUSE_ASSET = "lakehouse.tar.gz"

# Landing tables a rebuild cannot afford to refetch. One entry today, and it is
# a hand-written copy of a dlt resource name — `tests/test_restore_history.py`
# holds it to `ingest.pipeline`, because a renamed resource would otherwise stop
# being carried in silence and only cost a day of API budget a month later.

# What this warehouse cannot rebuild, and what proves each relation is the thing
# it claims to be. The package knows nothing about either — see `Carry`.
#
# The snapshot schema is carried whole: every table in `history` is there
# *because* it is unreproducible, so naming them would be a list to forget to
# update. `release-data.yml` learned that lesson once already, having asserted
# "history didn't shrink" against `snap_co2_estimates` by name until
# `snap_grid_emission_factors` arrived and was carried but never verified.
# **The `raw` rule is gone, and its job is not done — it moved out of reach.**
# `raw.om_weather_daily` was carried here because it is unreproducible within
# Open-Meteo's daily budget. It is still unreproducible; it is simply no longer
# in this file. dlt lands `raw` in the DuckLake catalog under `data/lakehouse/`,
# which the release does not publish, so there is nothing in a published
# `warehouse.duckdb` for this rule to find.
#
# The consequence is quiet and expensive, which is why it is written here rather
# than left to be discovered: `weather_watermark()` reads the destination table,
# a fresh runner's catalog is empty, and every release therefore cold-starts the
# archive at `WEATHER_COLD_START_YEARS`. Nothing errors — a cold start is a valid
# state — and the published series silently stops deepening.
#
# Carrying it forward again means publishing the lakehouse as a second release
# asset and restoring it before the graph runs. That is *simpler* than what this
# module does, not harder: a DuckLake is a directory, so the restore is a copy
# rather than a schema-aware `create or replace`. What it needs first is the
# decision to publish it, and one measured detail — the catalog stores its
# `data_path` as given, so a published one has to be created with a relative
# path (verified: relative survives a move with a bare ATTACH; absolute needs
# `OVERRIDE_DATA_PATH`).
CARRIED: tuple[Carry, ...] = (
    Carry(schema=HISTORY_SCHEMA, kind="dbt snapshot", required_columns=SCD2_COLUMNS),
)

__all__ = [
    "CARRIED",
    "DUCKDB_PATH",
    "HISTORY_SCHEMA",
    "LAKEHOUSE_ASSET",
    "RAW_SCHEMA",
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

    Restoring nothing is a normal outcome — the first release ever cut has no
    predecessor, and a source holding none of `CARRIED` is the same case. Only a
    *destination* that already holds them is an error.

    **Refuses when a landing table would be carried and dlt already has local
    state**, because the two together do not work — see `_refuse_warm_state`.
    That refusal is asked *first*, before the snapshot is touched: this function
    writes two artifacts and only one of them can be rolled back by not writing
    it. Raising from inside the second step used to leave the `history` schema
    already replaced by the previous release's — a partial restore that both this
    docstring and CLAUDE.md described as a refusal.
    """
    src = Path(source)
    archive = src.parent / LAKEHOUSE_ASSET
    if archive.exists():
        # Only when there is a landing zone in the release. A history-only
        # restore never creates the `raw` schema, so dlt's local state is not
        # contradicted and must not be refused over — the recipe that already
        # worked has to keep working, which `tests/test_restore_history.py` pins.
        from lake import lakehouse

        lakehouse.preflight(lakehouse.LAKEHOUSE_DIR if lakehouse_dir is None else lakehouse_dir)

    summary = restore(src, duckdb_path, carry=CARRIED, force=force)
    summary["lakehouse"] = _restore_lakehouse(src, lakehouse_dir)
    return summary


def _restore_lakehouse(source: Path, lakehouse_dir: str | Path | None) -> dict[str, int]:
    """Carry the published landing zone in beside the snapshot, if there is one.

    Both halves of the release are unreproducible and they are unreproducible for
    different reasons — `history` in principle, the weather archive within a
    budget — so one command carries both and a release either has the pair or has
    neither. The lakehouse sits next to the database in the release, so it is
    found rather than named: a `--lakehouse` flag would be a second thing to get
    right on a path that is already fixed by the export's own layout.

    A release that predates the lakehouse asset simply has no directory, which is
    the same "restoring nothing is a normal outcome" rule the snapshot follows.

    `run()` has already asked `lakehouse.preflight` about this same archive, so
    by the time this is reached the refusals have passed. `lakehouse.restore`
    asks again — it is a public entry point in its own right and cannot assume a
    caller checked — and the second answer costs a `stat` and a row count.
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

    The question `just clean warehouse` asks before deleting the file, and the
    question `release-data.yml` asks on both sides of the build. One function so
    that adding a rule to `CARRIED` updates all three at once: the gate that used
    to count `history` alone would have waved through the deletion of a weather
    archive that costs days of API budget to refetch.
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
