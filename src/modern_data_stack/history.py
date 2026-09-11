"""Carry the tables a rebuild cannot reproduce forward from a published database.

Most of a warehouse is disposable: delete it, run the pipeline, get it back. A
few tables are not, and they are not all unreproducible for the same reason.

* A **dbt snapshot** is state *in principle*. `dbt build` appends to it and no
  rebuild can invent a revision that upstream has since overwritten.
* A **rate-limited landing table** is unreproducible within a *budget*, which is
  a weaker claim with the same consequence: the data exists upstream and cannot
  be fetched again this month.

A project whose CI builds from an empty file would otherwise republish the thin
version forever, so this copies those relations out of the previous release
into the fresh database *before* the graph runs — dbt appends to a snapshot
during `dbt build`, so the old rows must already be there. A dlt destination
holding only carried *data* tables is still judged fresh (dlt reads its own
bookkeeping tables), which is why a rule over a landing schema must name its
tables rather than copy that bookkeeping too.

It refuses to overwrite a destination table that holds rows, unless `force`.
And it checks each source relation carries the columns that prove what it is:
dbt's SCD2 columns for a snapshot, dlt's for a landing table. Otherwise the
failure comes later and less legibly — inside `dbt build`, or at the next load
as DuckDB's `Adding columns with constraints not yet supported`.
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

    `tables=None` means every table in the schema — right only for a schema
    that holds nothing but unreproducible state. `kind` is prose for the refusal
    message, naming what the caller meant the relation to be.
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

    A named table the source lacks is skipped: the first release to carry a new
    table has a predecessor without it.
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

    `carry` has no default: what a project cannot reproduce is its own fact.
    Restoring nothing is normal (the first release has no predecessor); only a
    destination that already holds the relations is an error.
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

    One function, so the carried-in count, the did-it-shrink assertion and the
    anything-to-lose gate cannot drift apart as the rules change.
    """
    prefix = f"{database}." if database else ""
    counts = {}
    for rule in carry:
        for name in _wanted(con, database or scalar(con, "select current_database()"), rule):
            counts[f"{rule.schema}.{name}"] = _rows(con, f'{prefix}{rule.schema}."{name}"')
    return counts
