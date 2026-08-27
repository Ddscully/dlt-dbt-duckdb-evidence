"""Attach a DuckLake catalog, and read what changed between two snapshots.

DuckLake is a table format: plain Parquet under a data directory, plus a
catalog database holding schema, snapshot lineage and per-file statistics. This
module is the domain-neutral half — attaching one, listing its snapshots, and
diffing a table across two of them. What lives in the lakehouse, and where it
sits, is `lake/lakehouse.py`.

## Why the diff is here rather than `ducklake_table_changes()`

DuckLake ships a change feed that returns `insert` / `delete` /
`update_preimage` / `update_postimage` at row grain, and it is the obvious
answer. It is the wrong one whenever the writer rewrites rows it did not change
— which dlt does on every merge, regenerating `_dlt_id` and `_dlt_load_id` per
row. Measured: 500 identical rows loaded a second time report 500 preimages and
500 postimages. The feed is faithful; the *writer* is what makes it useless.

So `revisions()` compares the table at two versions with `EXCEPT`, projecting
away whichever columns the caller says are provenance. `ignore` has **no
default**, matching `db.write_frames`'s `schema` and `export`'s
`max_storage_version`: which columns are bookkeeping is a fact about the writer,
this module knows nothing about it, and a wrong default is invisible — it
returns a plausible answer rather than an error.

## What `read_parquet` over the data directory is not

It is not the table, in either configuration, and this is worth stating because
the directory looks like a hive archive and invites the shortcut. At DuckLake's
default `data_inlining_row_limit` of 10 a small change is written into the
catalog *database*, so the files silently return the superseded value. At 0 it
reaches Parquet, but so does a `…-delete.parquet` of `(file_path, pos)`, which
makes a glob fail on the schema mismatch and — if excluded by name — returns
**both** versions of the row. The catalog is not optional.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from .db import scalar

__all__ = [
    "attach",
    "publish",
    "revisions",
    "row_count",
    "set_data_path",
    "snapshots",
    "table_versions",
]


def meta_alias(alias: str) -> str:
    """The name DuckLake attaches the catalog database under, alongside the lake.

    Attaching the catalog file a second time by hand is what this replaced, and
    DuckDB refuses it outright: `Unique file handle conflict: Cannot attach
    "lakehouse_meta" - the database file … is already attached by database
    "__ducklake_metadata_lakehouse"`. The catalog is already there; the name is
    just undocumented enough to be worth writing down once.
    """
    return f"__ducklake_metadata_{alias}"


def attach(
    con: duckdb.DuckDBPyConnection,
    catalog_path: str | Path,
    data_path: str | Path,
    alias: str,
    read_only: bool = False,
    data_inlining_row_limit: int | None = None,
) -> None:
    """Attach the DuckLake at `catalog_path` as `alias`, and its catalog beside it.

    `data_path` is passed on every attach even though the catalog already
    records it, because DuckLake *checks* the two agree and refuses otherwise —
    a mismatch is a moved lakehouse, which is a thing worth being told about
    rather than silently reading someone else's files.

    **The catalog database comes along for free**, attached by DuckLake itself
    as `__ducklake_metadata_<alias>` — see `meta_alias`. It is needed because
    DuckLake's *query* surface cannot answer "which snapshots
    changed *this table*": `snapshots()` returns a `changes` map keyed on table
    *ids*, and `table_changes(name, from, to)` needs the answer as its argument
    — it raises `Table … does not exist at version N` for a range that starts
    before the table did. The catalog schema is part of the DuckLake 1.0 spec
    rather than an internal, so `ducklake_data_file` is a supported place to
    read it from, and `table_versions` below is the only thing that does.
    """
    con.execute("install ducklake")
    con.execute("load ducklake")

    options = [f"data_path '{Path(data_path)}/'"]
    if read_only:
        options.append("read_only")
    if data_inlining_row_limit is not None:
        options.append(f"data_inlining_row_limit {int(data_inlining_row_limit)}")

    # ATTACH takes literals, not bind parameters — `attach $path` is a parser
    # error — so the paths are interpolated, as they are in `history.restore`.
    con.execute(f"attach 'ducklake:duckdb:{Path(catalog_path)}' as {alias} ({', '.join(options)})")


def snapshots(con: duckdb.DuckDBPyConnection, alias: str) -> list[int]:
    """Every snapshot id in the catalog, oldest first."""
    return [
        row[0] for row in con.execute(f"select snapshot_id from {alias}.snapshots()").fetchall()
    ]


def table_versions(con: duckdb.DuckDBPyConnection, alias: str, table: str) -> list[int]:
    """Snapshots in which `table` actually changed, oldest first.

    The catalog records a snapshot per *write*, and a dlt load performs several
    — staging tables, the merge, cleanup — so most snapshot ids say nothing
    about any given table. Asking which ones touched it is what makes "compare
    against the previous version" mean the previous version *of this table*
    rather than of whatever else happened to be written in between.
    """
    schema, name = _split(table)
    meta = meta_alias(alias)
    ids = [
        row[0]
        for row in con.execute(
            f"""
            select t.table_id
            from {meta}.ducklake_table t
            join {meta}.ducklake_schema s on s.schema_id = t.schema_id
            where t.table_name = $table and s.schema_name = $schema
            """,
            {"table": name, "schema": schema},
        ).fetchall()
    ]
    if not ids:
        return []
    id_list = ", ".join(str(int(i)) for i in ids)

    # **Both halves are required, and the missing one fails silently.** DuckLake
    # writes a change of `data_inlining_row_limit` rows or fewer (default 10)
    # into the catalog instead of out to Parquet, so a small load leaves no
    # `ducklake_data_file` row at all — and a version list built from files
    # alone simply does not see it. That is not a test-only case: an FX day is
    # ~29 rows but a quiet one is fewer, and the symptom is a revision log that
    # skips a load rather than one that errors.
    sources = [
        f"select begin_snapshot from {meta}.ducklake_data_file where table_id in ({id_list})"
    ]
    inlined = con.execute(
        f"select table_name from {meta}.ducklake_inlined_data_tables where table_id in ({id_list})"
    ).fetchall()
    sources += [f'select begin_snapshot from {meta}."{row[0]}"' for row in inlined]

    rows = con.execute(
        f"select distinct begin_snapshot from ({' union all '.join(sources)}) order by 1"
    ).fetchall()
    return [row[0] for row in rows]


def revisions(
    con: duckdb.DuckDBPyConnection,
    alias: str,
    table: str,
    since: int,
    until: int | None = None,
    ignore: tuple[str, ...] = (),
) -> list[tuple]:
    """Rows of `table` at `until` that are not present, identically, at `since`.

    An insert and an update are both "a row here that was not there before", and
    the caller almost always wants both — this is "what does the table say now
    that it did not say then", which is the question a restatement log answers.
    Deletions are the mirror image and are not returned; swap the arguments.
    """
    columns = [c for c in _columns(con, alias, table) if c not in ignore]
    if not columns:
        raise ValueError(f"{table} has no columns left to compare after ignoring {ignore}")
    projection = ", ".join(f'"{c}"' for c in columns)

    at_until = "" if until is None else f" at (version => {until})"
    return con.execute(
        f"""
        select {projection} from {alias}.{table}{at_until}
        except
        select {projection} from {alias}.{table} at (version => {since})
        """
    ).fetchall()


def _split(table: str) -> tuple[str, str]:
    if table.count(".") != 1:
        raise ValueError(f"{table!r} is not a schema-qualified table name")
    schema, name = table.split(".")
    return schema, name


def _columns(con: duckdb.DuckDBPyConnection, alias: str, table: str) -> list[str]:
    schema, name = _split(table)
    rows = con.execute(
        """
        select column_name from information_schema.columns
        where table_catalog = $catalog and table_schema = $schema and table_name = $table
        order by ordinal_position
        """,
        {"catalog": alias, "schema": schema, "table": name},
    ).fetchall()
    if not rows:
        raise ValueError(f"{alias}.{table} does not exist")
    return [row[0] for row in rows]


def row_count(con: duckdb.DuckDBPyConnection, alias: str, table: str) -> int:
    return scalar(con, f"select count(*) from {alias}.{table}")


def publish(
    con: duckdb.DuckDBPyConnection,
    source_alias: str,
    dest_dir: str | Path,
    tables: tuple[str, ...],
    data_dirname: str,
    catalog_name: str,
) -> dict[str, int]:
    """Build a **relocatable** DuckLake at `dest_dir` holding only `tables`.

    Two properties, and both are the point.

    **It is built, never filtered.** Copying the catalog and dropping what should
    not ship does not work: DuckLake keeps dropped tables in earlier snapshots,
    so `select * from lh.raw.secret at (version => 2)` returns the rows after the
    drop — verified, and it returned a customer id. A published catalog therefore
    contains what it was *created* with, and an allowlist is the only safe shape.
    The cost is that snapshot lineage does not survive: the published catalog has
    one version per table, and the accumulation happens in the rows.

    **Its `data_path` is relative**, which is what makes it openable by someone
    who is not us. DuckLake stores the path as given and checks it on every
    attach, so an absolute one forces `OVERRIDE_DATA_PATH` on every consumer. The
    catalog cannot simply be *created* relative here — DuckDB resolves it against
    the process's working directory, not the file's — so it is created absolute
    and the single `ducklake_metadata` row is rewritten afterwards. The per-file
    paths were relative all along.
    """
    dest = Path(dest_dir)
    (dest / data_dirname).mkdir(parents=True, exist_ok=True)
    catalog = dest / catalog_name
    if catalog.exists():
        catalog.unlink()

    attach(con, catalog, dest / data_dirname, alias="_publish")
    copied = {}
    try:
        for table in tables:
            schema, name = _split(table)
            # A table the source has not got is skipped, not an error. Two ways
            # that happens and both are normal: the first release after a table
            # is added has a source that predates it, and dbt's
            # `ATTACH IF NOT EXISTS` creates an empty catalog on any build that
            # runs before the first ingest.
            if not _exists(con, source_alias, schema, name):
                continue
            con.execute(f"create schema if not exists _publish.{schema}")
            con.execute(
                f'create table _publish.{schema}."{name}" as '
                f'select * from {source_alias}.{schema}."{name}"'
            )
            copied[table] = scalar(con, f'select count(*) from _publish.{schema}."{name}"')
    finally:
        con.execute("detach _publish")

    set_data_path(catalog, f"{data_dirname}/")
    return copied


def set_data_path(catalog_path: str | Path, data_path: str) -> None:
    """Rewrite the one absolute-or-relative row that decides who can open a catalog.

    DuckLake stores `data_path` exactly as it was given and **checks it on every
    attach**, refusing a mismatch rather than trusting the caller. That check is
    the right behaviour — a mismatch means someone moved the lakehouse — but it
    means a published catalog and a working one want *opposite* forms of the same
    path, and there is no form that serves both:

    * **relative** (`data/`) opens with a bare `ATTACH` from the directory it was
      unpacked into, which is the only thing a consumer can be asked to do;
    * **absolute** is what a working copy needs, because dlt reads it from the
      repo root and dbt from `dbt/`, and one relative path cannot mean the same
      thing in both.

    So the form is rewritten at each boundary — `publish` on the way out, and the
    restoring caller on the way in. The per-file paths are relative in both, so
    this single row is the whole of it.
    """
    meta = duckdb.connect(str(Path(catalog_path)))
    try:
        meta.execute(
            "update ducklake_metadata set value = $path where key = 'data_path'",
            {"path": data_path},
        )
    finally:
        meta.close()


def _exists(con: duckdb.DuckDBPyConnection, alias: str, schema: str, name: str) -> bool:
    return bool(
        con.execute(
            """
            select 1 from information_schema.tables
            where table_catalog = $catalog and table_schema = $schema and table_name = $table
            """,
            {"catalog": alias, "schema": schema, "table": name},
        ).fetchone()
    )
