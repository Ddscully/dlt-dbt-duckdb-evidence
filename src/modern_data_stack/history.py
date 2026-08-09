"""Carry a dbt snapshot schema forward from a previously published database.

A snapshot is **state, not a build artifact**: `dbt build` appends to it and no
rebuild can reproduce it. So a project whose CI builds from an empty file
publishes a snapshot holding one version per row, forever — the one table a
rebuild cannot reproduce is the one table the published copy never has.

This is the other half: copy the snapshot schema out of the previous release into
the fresh database *before* the graph runs, so `dbt snapshot` compares this run's
numbers against the last release's and appends a version where upstream restated.

## Why before the build, and why pre-creating the file is safe

dbt appends during `dbt build`, so the previous rows have to be on disk before
that — which means writing the database file before dlt has created it. That is
fine: dlt keys "is this destination fresh?" on its own bookkeeping inside the raw
dataset, which a history-only file does not have, so it still performs a full
load. The restore never touches any schema but the snapshot's.

## What it refuses to do

Overwrite history that is already there. Deleting the warehouse is normally the
only destructive act in a project like this and this would be the second one, so
a destination table with rows in it stops the restore unless `force`. It also
checks the source carries dbt's SCD2 bookkeeping columns, because restoring
something snapshot-shaped-but-not-a-snapshot fails later, inside dbt, with a much
worse error message.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

DEFAULT_HISTORY_SCHEMA = "history"

# dbt's SCD2 bookkeeping. Without these the relation is not a snapshot dbt can
# merge into, whatever else it holds.
SCD2_COLUMNS = ("dbt_scd_id", "dbt_updated_at", "dbt_valid_from", "dbt_valid_to")


def _tables(con: duckdb.DuckDBPyConnection, database: str, schema: str) -> list[str]:
    """Table names in `<database>.<schema>`, or [] if the schema isn't there."""
    rows = con.execute(
        """
        select table_name
        from duckdb_tables()
        where database_name = $database and schema_name = $schema
        order by table_name
        """,
        {"database": database, "schema": schema},
    ).fetchall()
    return [name for (name,) in rows]


def _columns(con: duckdb.DuckDBPyConnection, qualified: str) -> set[str]:
    return {row[0] for row in con.execute(f"describe {qualified}").fetchall()}


def _rows(con: duckdb.DuckDBPyConnection, qualified: str) -> int:
    (count,) = con.execute(f"select count(*) from {qualified}").fetchone()
    return count


def restore(
    source: str | Path,
    duckdb_path: str | Path,
    force: bool = False,
    history_schema: str = DEFAULT_HISTORY_SCHEMA,
) -> dict:
    """Copy `source`'s snapshot schema into `duckdb_path`. Returns a summary.

    Restoring nothing is a normal outcome — the first release ever cut has no
    predecessor, and a source with no snapshot schema (or an empty one) is the
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
            restored = _restore_tables(con, force=force, history_schema=history_schema)
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


def _restore_tables(con: duckdb.DuckDBPyConnection, force: bool, history_schema: str) -> list[dict]:
    names = _tables(con, "prev_wh", history_schema)
    if not names:
        return []

    current = con.execute("select current_database()").fetchone()[0]
    existing = set(_tables(con, current, history_schema))
    con.execute(f"create schema if not exists {history_schema}")

    restored = []
    for name in names:
        source_relation = f'prev_wh.{history_schema}."{name}"'
        dest_relation = f'{history_schema}."{name}"'

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
        restored.append({"table": f"{history_schema}.{name}", "rows": rows})

    return restored
