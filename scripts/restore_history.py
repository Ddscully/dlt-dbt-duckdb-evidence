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
    DLT_COLUMNS,
    SCD2_COLUMNS,
    Carry,
    carried_rows,
    restore,
)
from modern_data_stack.paths import warehouse_path

DUCKDB_PATH = warehouse_path()

HISTORY_SCHEMA = "history"
RAW_SCHEMA = "raw"

# Landing tables a rebuild cannot afford to refetch. One entry today, and it is
# a hand-written copy of a dlt resource name — `tests/test_restore_history.py`
# holds it to `ingest.pipeline`, because a renamed resource would otherwise stop
# being carried in silence and only cost a day of API budget a month later.
CARRIED_RAW_TABLES = ("om_weather_daily",)

# What this warehouse cannot rebuild, and what proves each relation is the thing
# it claims to be. The package knows nothing about either — see `Carry`.
#
# The snapshot schema is carried whole: every table in `history` is there
# *because* it is unreproducible, so naming them would be a list to forget to
# update. `release-data.yml` learned that lesson once already, having asserted
# "history didn't shrink" against `snap_co2_estimates` by name until
# `snap_grid_emission_factors` arrived and was carried but never verified.
CARRIED: tuple[Carry, ...] = (
    Carry(schema=HISTORY_SCHEMA, kind="dbt snapshot", required_columns=SCD2_COLUMNS),
    # `raw` is the opposite case and `tables` is not optional here. The schema
    # holds seven other landing tables and dlt's own bookkeeping, and carrying
    # that bookkeeping is what would make the next load think the destination
    # was not fresh — so this rule names exactly what it wants and nothing else.
    #
    # `DLT_COLUMNS` is sufficient because `restore` copies with `select *`: the
    # two columns are the ones a merge resource needs to land on an existing
    # table, and a source that has them keeps everything else for free. Without
    # them the next load dies at the load step with DuckDB's `Adding columns
    # with constraints not yet supported` — dlt trying to add `_dlt_load_id`
    # NOT NULL to a table that already has rows.
    Carry(
        schema=RAW_SCHEMA,
        kind="dlt landing table",
        required_columns=DLT_COLUMNS,
        tables=CARRIED_RAW_TABLES,
    ),
)

__all__ = [
    "CARRIED",
    "CARRIED_RAW_TABLES",
    "DUCKDB_PATH",
    "HISTORY_SCHEMA",
    "RAW_SCHEMA",
    "irreplaceable_rows",
    "main",
    "run",
]


def _source_landing_tables(source: Path) -> set[str]:
    """Which of `CARRIED_RAW_TABLES` the source actually holds."""
    con = duckdb.connect(str(source), read_only=True)
    try:
        present = {
            name
            for (name,) in con.execute(
                "select table_name from duckdb_tables() where schema_name = ?",
                [RAW_SCHEMA],
            ).fetchall()
        }
    finally:
        con.close()
    return present & set(CARRIED_RAW_TABLES)


def _local_pipeline_state() -> Path | None:
    """dlt's state directory for this project's pipeline, if it has one.

    Read rather than built: constructing the pipeline to ask its name is what
    would create the state this is looking for.
    """
    from dlt.common.pipeline import get_dlt_pipelines_dir

    from ingest.pipeline import pipeline_name

    state = Path(get_dlt_pipelines_dir()) / pipeline_name()
    return state if state.exists() else None


def run(
    source: str | Path,
    duckdb_path: str | Path = DUCKDB_PATH,
    force: bool = False,
) -> dict:
    """Copy `source`'s unreproducible tables into `duckdb_path`. Returns a summary.

    Restoring nothing is a normal outcome — the first release ever cut has no
    predecessor, and a source holding none of `CARRIED` is the same case. Only a
    *destination* that already holds them is an error.

    **Refuses when a landing table would be carried and dlt already has local
    state**, because the two together do not work — see `_refuse_warm_state`.
    """
    src = Path(source)
    if src.exists() and _source_landing_tables(src):
        state = _local_pipeline_state()
        if state is not None:
            _refuse_warm_state(state)
    return restore(source, duckdb_path, carry=CARRIED, force=force)


def _refuse_warm_state(state: Path) -> None:
    """Stop before a restore that dlt's local state would make fail.

    Carrying a table into `raw` *creates that schema*, and dlt then finds a
    dataset its own bookkeeping does not describe. What happens next depends
    entirely on whether dlt has local state, which is why this is invisible in
    CI and immediate on a laptop:

    * **No local state** (a fresh runner — `release-data.yml`, `pages.yml`,
      `nightly.yml`) — dlt queries the destination, finds no `_dlt_version`,
      concludes the dataset is new and creates its bookkeeping. The carried rows
      survive and the merge lands on them. This is the path the whole
      carry-forward is built for, and it is measured: 44,936 carried weather
      rows through a full load.
    * **Local state** (any machine that has run `just ingest`) — dlt trusts what
      it already knows, goes to update its stored schema and dies with
      `Table with name _dlt_version does not exist!`.

    Carrying dlt's bookkeeping along is *worse*, not better: dlt then believes
    every table the schema describes is present, and the other merge resources
    fail on the ones that were never carried (`DELETE FROM "raw"."ecb_fx_rates"
    … does not exist`). `sync_destination()` does not help either — it keeps the
    local schema rather than deferring to the destination.

    So the remedy is to drop the state, not to work around it. That is also the
    honest answer: a restored file is a destination the local state has never
    described, and dlt already resets itself when a destination is empty.
    """
    raise RuntimeError(
        f"dlt has local pipeline state at {state}, and this restore would carry a "
        f"landing table into `{RAW_SCHEMA}`. dlt would then look for bookkeeping the "
        "restored file does not have and fail with `Table with name _dlt_version "
        "does not exist!`. Drop the state first — it describes a destination this "
        f"restore is replacing:\n    rm -rf {state}\n"
        "The next load re-fetches what that state was tracking (WDI's watermark), "
        "which is eleven requests and free."
    )


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
    if not summary["tables"]:
        print(f"nothing to carry forward from {summary['source']} — starting from empty")
        return
    print(f"restored into {summary['destination']} from {summary['source']}:")
    for table in summary["tables"]:
        print(f"  {table['table']:32} {table['rows']:>8,} rows")


if __name__ == "__main__":
    main()
