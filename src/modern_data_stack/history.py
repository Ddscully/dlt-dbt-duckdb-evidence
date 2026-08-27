"""Carry the tables a rebuild cannot reproduce forward from a published database.

Most of a warehouse is disposable: delete it, run the pipeline, get it back. A
few tables are not, and they are not all unreproducible for the same reason.

* A **dbt snapshot** is state *in principle*. `dbt build` appends to it and no
  rebuild can invent a revision that upstream has since overwritten.
* A **rate-limited landing table** is unreproducible within a *budget*, which is
  a weaker claim with the same consequence: the data exists upstream and cannot
  be fetched again this month.

Either way a project whose CI builds from an empty file republishes the thin
version forever. This module is the other half: copy those relations out of the
previous release into the fresh database *before* the graph runs.

## Why before the build, and why pre-creating the file is safe

dbt appends during `dbt build`, so the previous rows have to be on disk before
that — which means writing the database file before dlt has created it. That is
fine: dlt keys "is this destination fresh?" on its own bookkeeping tables, so a
file holding only carried *data* tables still gets a full load. **This is also
why a carry rule over a landing schema must name its tables**: copying that
schema wholesale would bring dlt's bookkeeping with it and change the answer.

## What it refuses to do

Overwrite what is already there. Deleting the warehouse is normally the only
destructive act in a project like this and this would be the second one, so a
destination table with rows in it stops the restore unless `force`.

It also checks each source relation carries the bookkeeping columns that make it
the thing the caller thinks it is — **and the two kinds look for opposite
things**. A snapshot proves itself with dbt's SCD2 columns; a landing table
proves itself with dlt's. Both failures are otherwise deferred and much less
legible: a snapshot-shaped-but-not-a-snapshot dies inside `dbt build`, and a
landing table missing `_dlt_load_id` dies at the next load with DuckDB's
`Adding columns with constraints not yet supported`, because dlt tries to add
the column `NOT NULL` to a table that already has rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

from .db import scalar

# dbt's SCD2 bookkeeping. Without these the relation is not a snapshot dbt can
# merge into, whatever else it holds.
SCD2_COLUMNS = ("dbt_scd_id", "dbt_updated_at", "dbt_valid_from", "dbt_valid_to")

# dlt's per-row provenance. Without these a merge resource cannot land on the
# carried table at all — see the module docstring.
DLT_COLUMNS = ("_dlt_load_id", "_dlt_id")


@dataclass(frozen=True)
class Carry:
    """One rule: which relations to copy forward, and what proves they qualify.

    `tables=None` means every table in the schema, which is right for a schema
    that exists *only* to hold unreproducible state. Naming tables explicitly is
    required wherever the schema holds anything else — a landing schema, most
    obviously, where copying the whole thing would carry dlt's own bookkeeping
    and make the next load think the destination was not fresh.

    `kind` is prose, and it is only ever read in the refusal message. It is a
    field rather than a lookup off `required_columns` so that the message names
    what the caller *meant*, which is the thing a person can act on.
    """

    schema: str
    kind: str
    required_columns: tuple[str, ...]
    tables: tuple[str, ...] | None = None


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
    return scalar(con, f"select count(*) from {qualified}")


def _wanted(con: duckdb.DuckDBPyConnection, database: str, rule: Carry) -> list[str]:
    """The tables `rule` selects that the source actually has.

    A named table the source lacks is **skipped, not an error**. The release
    that first carries a new table has a predecessor that predates it, so
    treating the gap as fatal would make exactly one release fail — and it is
    the same "restoring nothing is a normal outcome" rule, one level down.
    """
    present = _tables(con, database, rule.schema)
    if rule.tables is None:
        return present
    return [name for name in rule.tables if name in present]


def restore(
    source: str | Path,
    duckdb_path: str | Path,
    carry: tuple[Carry, ...],
    force: bool = False,
) -> dict:
    """Copy the relations `carry` names out of `source` into `duckdb_path`.

    `carry` has no default, matching `db.write_frames`'s `schema` and
    `export`'s `max_storage_version`: what a project cannot reproduce is a fact
    about that project, and this module knows nothing about it. A default would
    be invisible to the caller that means something else, and `create or
    replace` does not ask twice.

    Restoring nothing is a normal outcome — the first release ever cut has no
    predecessor, and a source with none of the named relations is the same case.
    Only a *destination* that already holds them is an error.
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
            restored = _restore_tables(con, carry=carry, force=force)
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


def _restore_tables(
    con: duckdb.DuckDBPyConnection, carry: tuple[Carry, ...], force: bool
) -> list[dict]:
    current = scalar(con, "select current_database()")
    restored = []

    for rule in carry:
        names = _wanted(con, "prev_wh", rule)
        if not names:
            continue

        existing = set(_tables(con, current, rule.schema))
        con.execute(f"create schema if not exists {rule.schema}")

        for name in names:
            source_relation = f'prev_wh.{rule.schema}."{name}"'
            dest_relation = f'{rule.schema}."{name}"'

            missing = [c for c in rule.required_columns if c not in _columns(con, source_relation)]
            if missing:
                raise ValueError(
                    f"{source_relation} is not a {rule.kind} — no {', '.join(missing)}. "
                    "Carrying it forward would fail later, and much less legibly."
                )

            rows = _rows(con, source_relation)
            if not rows:
                continue

            if name in existing and not force:
                held = _rows(con, dest_relation)
                if held:
                    raise ValueError(
                        f"{dest_relation} already holds {held:,} rows — refusing to "
                        "overwrite state that a rebuild cannot reproduce. Pass "
                        "--force if replacing it is really what you want."
                    )

            con.execute(
                f"create or replace table {dest_relation} as select * from {source_relation}"
            )
            restored.append({"table": f"{rule.schema}.{name}", "rows": rows})

    return restored


def carried_rows(
    con: duckdb.DuckDBPyConnection, carry: tuple[Carry, ...], database: str | None = None
) -> dict[str, int]:
    """Rows currently held in each relation `carry` names, for the guards.

    One function so that the "how much did we carry" count, the "did it shrink"
    assertion and the "is there anything here to lose" gate cannot drift apart
    as the rules change — which is the failure the release workflow already
    documents for a snapshot added later and never verified.
    """
    prefix = f"{database}." if database else ""
    counts = {}
    for rule in carry:
        for name in _wanted(con, database or scalar(con, "select current_database()"), rule):
            counts[f"{rule.schema}.{name}"] = _rows(con, f'{prefix}{rule.schema}."{name}"')
    return counts
