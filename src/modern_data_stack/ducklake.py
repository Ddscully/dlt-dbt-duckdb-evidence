"""Attach a DuckLake catalog, and read what changed between two snapshots.

DuckLake is a table format: plain Parquet under a data directory, plus a
catalog database holding schema, snapshot lineage and per-file statistics. This
module is the domain-neutral half — attaching one, listing its snapshots, and
diffing a table across two of them. What lives in the lakehouse, and where it
sits, is `lake/lakehouse.py`.

## Why the diff is here rather than `ducklake_table_changes()`

DuckLake's change feed is faithful to the writer, and useless when the writer
rewrites unchanged rows — dlt regenerates `_dlt_id` and `_dlt_load_id` on every
merge, so 500 identical rows reloaded report 500 updates. `revisions()` compares
two versions with `EXCEPT` instead, projecting away the columns the caller names
as provenance. `ignore` names columns the writer owns, so the caller supplies it.

## `read_parquet` over the data directory is not the table

At the default `data_inlining_row_limit` of 10 a small change is written into
the catalog database, so the files return the superseded value. At 0 it reaches
Parquet, but with a `…-delete.parquet` of `(file_path, pos)` that breaks a glob's
schema — or, excluded by name, returns both versions of the row. Read through
the catalog.
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
    "spec_version",
    "table_versions",
]


def meta_alias(alias: str) -> str:
    """The (undocumented) name DuckLake attaches the catalog database under.

    Attaching the catalog file again under another name is refused as a
    `Unique file handle conflict`; it is already attached under this one.
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

    `data_path` is passed although the catalog records it, because DuckLake
    checks the two agree and refuses a mismatch (a moved lakehouse).

    DuckLake also attaches the catalog database, as `meta_alias(alias)`.
    `table_versions` reads it, because the query surface cannot say which
    snapshots changed one table; the catalog schema is part of the DuckLake 1.0
    spec, not an internal.
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

    A dlt load writes several snapshots (staging, merge, cleanup), so most say
    nothing about a given table; this is what makes "the previous version" mean
    the previous version of *this* table.
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

    # Files *and* inlined data: a change of `data_inlining_row_limit` rows or
    # fewer (default 10) is written into the catalog, leaving no
    # `ducklake_data_file` row, and a file-only list silently skips that load.
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

    Inserts and updates alike — what the table says now that it did not then.
    Deletions are not returned; swap the arguments for those.
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
    max_spec_version: str | None = None,
) -> dict[str, int]:
    """Build a **relocatable** DuckLake at `dest_dir` holding only `tables`.

    **Built, never filtered**: DuckLake keeps dropped tables readable in earlier
    snapshots (`at (version => …)`), so a copied-then-pruned catalog still ships
    what was dropped. The cost is that snapshot lineage does not survive.

    **Its `data_path` is relative**, so a consumer can open it with a bare
    `ATTACH` rather than `OVERRIDE_DATA_PATH`. DuckDB resolves a relative path
    against the process's cwd at creation, so the catalog is created absolute
    and the `ducklake_metadata` row rewritten after; per-file paths are already
    relative.

    `max_spec_version` is the spec ceiling; no default, for `export`'s reason.
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
            # Skipped, not an error: a source can predate a newly listed table,
            # and dbt's `ATTACH IF NOT EXISTS` creates an empty catalog before
            # the first ingest.
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

    # Measured on the catalog just built — what ships, written by this machine's
    # DuckLake, whatever the source was written with. `>`: publishing at the
    # ceiling is ordinary. As in `export`, the directory is left for inspection.
    published = spec_version(catalog)
    if max_spec_version is not None and version_key(published) > version_key(max_spec_version):
        raise ValueError(
            f"refusing to publish {catalog.name}: DuckLake spec version "
            f"{published}, above the {max_spec_version} this project promises. "
            "The extension that wrote it is a binary from extensions.duckdb.org "
            "that no lockfile can name, so nothing else in this repo can notice "
            "the format moving. Raising the ceiling strands every consumer whose "
            "ducklake is older than the new spec."
        )
    return copied


def catalog_metadata(catalog_path: str | Path) -> dict[str, str]:
    """Everything `ducklake_metadata` records about a catalog, as a map.

    `version` is the spec a consumer needs; `created_by` the DuckDB build that
    wrote it. Read from the catalog file directly, read-only, so it can
    describe an unpacked artifact that nothing has attached.
    """
    path = Path(catalog_path)
    try:
        con = duckdb.connect(str(path), read_only=True)
    except duckdb.Error as exc:  # not a database at all
        raise ValueError(f"not a DuckLake catalog: {path}") from exc
    try:
        rows = con.execute("select key, value from ducklake_metadata").fetchall()
    except duckdb.Error as exc:  # a DuckDB file, but not a catalog
        raise ValueError(f"not a DuckLake catalog: {path}") from exc
    finally:
        con.close()
    return {str(key): str(value) for key, value in rows}


def spec_version(catalog_path: str | Path) -> str:
    """The DuckLake spec version a catalog was written against.

    The catalog counterpart of `export.storage_version`: whether a consumer can
    open it. Returned as the string recorded (`1.0`); `version_key` orders two.
    """
    meta = catalog_metadata(catalog_path)
    if "version" not in meta:
        raise ValueError(f"DuckLake catalog with no recorded version: {catalog_path}")
    return meta["version"]


def version_key(version: str) -> tuple[int, ...]:
    """A sort key for a dotted version: `1.10` above `1.9`, unlike a string."""
    return tuple(int(part) for part in version.split("."))


def set_data_path(catalog_path: str | Path, data_path: str) -> None:
    """Rewrite the catalog's `data_path`, which DuckLake checks on every attach.

    A published catalog wants it relative (a bare `ATTACH` wherever unpacked); a
    working one absolute (dlt and dbt run from different directories). So it is
    rewritten at each boundary — by `publish` on the way out, by the restoring
    caller on the way in. Per-file paths are relative in both.
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
