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

import duckdb

from ingest.pipeline import DUCKDB_PATH

HISTORY_SCHEMA = "history"

# dbt's SCD2 bookkeeping. `dbt_valid_from` is what `fct_co2_estimate_versions`
# orders versions by; without these the relation is not a snapshot dbt can merge
# into, whatever else it holds.
SCD2_COLUMNS = ("dbt_scd_id", "dbt_updated_at", "dbt_valid_from", "dbt_valid_to")


def _tables(con: duckdb.DuckDBPyConnection, database: str) -> list[str]:
    """Table names in `<database>.history`, or [] if the schema isn't there."""
    rows = con.execute(
        """
        select table_name
        from duckdb_tables()
        where database_name = $database and schema_name = $schema
        order by table_name
        """,
        {"database": database, "schema": HISTORY_SCHEMA},
    ).fetchall()
    return [name for (name,) in rows]


def _columns(con: duckdb.DuckDBPyConnection, qualified: str) -> set[str]:
    return {row[0] for row in con.execute(f"describe {qualified}").fetchall()}


def _rows(con: duckdb.DuckDBPyConnection, qualified: str) -> int:
    (count,) = con.execute(f"select count(*) from {qualified}").fetchone()
    return count


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
    src = Path(source)
    if not src.exists():
        raise FileNotFoundError(f"no warehouse to restore from at {src}")

    dest = Path(duckdb_path)
    if dest.resolve() == src.resolve():
        raise ValueError(f"source and destination are the same file: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(dest))
    try:
        # ATTACH takes a literal, not a bind parameter (`attach $path` is a
        # parser error), so the path is interpolated.
        con.execute(f"attach '{src}' as prev_wh (read_only)")
        try:
            restored = _restore_tables(con, force=force)
        finally:
            con.execute("detach prev_wh")
    finally:
        con.close()

    return {
        "source": str(src),
        "destination": str(dest),
        "tables": restored,
        "rows": sum(t["rows"] for t in restored),
    }


def _restore_tables(con: duckdb.DuckDBPyConnection, force: bool) -> list[dict]:
    names = _tables(con, "prev_wh")
    if not names:
        return []

    existing = set(_tables(con, con.execute("select current_database()").fetchone()[0]))
    con.execute(f"create schema if not exists {HISTORY_SCHEMA}")

    restored = []
    for name in names:
        source_relation = f'prev_wh.{HISTORY_SCHEMA}."{name}"'
        dest_relation = f'{HISTORY_SCHEMA}."{name}"'

        missing = [c for c in SCD2_COLUMNS if c not in _columns(con, source_relation)]
        if missing:
            raise ValueError(
                f"{source_relation} is not a dbt snapshot — no {', '.join(missing)}. "
                "Restoring it would fail inside `dbt build` instead of here."
            )

        rows = _rows(con, source_relation)
        if not rows:
            continue

        if name in existing and not force:
            held = _rows(con, dest_relation)
            if held:
                raise ValueError(
                    f"{dest_relation} already holds {held:,} rows — refusing to "
                    "overwrite history that a rebuild cannot reproduce. Pass "
                    "--force if replacing it is really what you want."
                )

        con.execute(f"create or replace table {dest_relation} as select * from {source_relation}")
        restored.append({"table": f"{HISTORY_SCHEMA}.{name}", "rows": rows})

    return restored


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
