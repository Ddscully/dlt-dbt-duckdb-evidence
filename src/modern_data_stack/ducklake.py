"""Keep a DuckLake lakehouse in step with warehouse tables, one `MERGE` per table.

The sibling of `lake.py`, answering the same question — *what moved upstream?* —
with a catalog instead of a directory layout. Both write plain Parquet; the
difference is what remembers.

## Why this is a `MERGE` and not the archive's write

`lake.archive` deletes each table's directory and writes it again, because
`COPY … (overwrite true)` only replaces the partitions it is *writing* and a
partition whose last row vanished upstream would otherwise keep answering
queries. Ported literally onto DuckLake that is a regression, measured before
this module was written: every run retires the old file and writes a whole new
one, and the catalog's change log reads **identically** for a run over unchanged
data and a run over a real restatement. Only reading the data separates them.
So the port would give up the archive's byte-diff property and replace it with
nothing, plus a file's worth of growth per table per run.

A `MERGE` gated on *the row actually differing* inverts that:

* nothing changed upstream — no rows touched, **no snapshot created at all**;
* a row was restated — one snapshot, and `ducklake_table_changes()` hands back
  the old and new values side by side as `update_preimage` / `update_postimage`;
* a row appeared or vanished — one snapshot, `insert` or `delete`.

That is strictly more than the hive archive can say. `sha256sum` over the
partitions reports *which file* differs; the change feed reports which row, and
what it used to be.

## Three things the format made non-obvious

**No partition column, deliberately.** DuckLake keeps per-file min/max
statistics in its catalog and prunes on them, so a filter still reads one file
without any directory layout to arrange it. Partitioning by year was measured at
2.4x the bytes of the same data unpartitioned (275 small Parquet files compress
far worse than one) and no faster. The archive has to choose a partition column
up front; this does not have to choose one at all.

**Upsert and prune are two statements.** DuckLake's `MERGE` supports a single
UPDATE/DELETE action, so `when matched … then update` and `when not matched by
source then delete` cannot share a statement — the second is a plain `DELETE`
here. Both are silent when they touch nothing, which is what matters: the
no-snapshot property survives the split.

**`RETURNING` is not implemented for DuckLake**, so the per-action breakdown is
read back out of the change feed rather than returned by the write. That is the
better source anyway: the counts in the summary are ones a consumer can
re-derive from the catalog months later, not ones that only existed in the
process that wrote them.

## The catalog is not optional, and that is the format's real cost

The hive archive's appeal is that anything reading Parquet can read it. A
DuckLake directory looks like it keeps that and does not, in **both** available
configurations — measured by restating one row and then reading the files
directly:

* **`data_inlining_row_limit` at DuckLake's default of 10** — the change is
  written into the catalog database and never reaches Parquet at all.
  `read_parquet` over the data directory returns the *superseded* value with no
  indication anything is missing.
* **At 0** — the change reaches Parquet, and so does a `…-delete.parquet` whose
  schema is `(file_path, pos)`. A glob over the directory now fails outright on
  the schema mismatch; excluding the delete files by name instead returns
  **both** versions of the row, because applying them positionally is the
  catalog's job.

So `read_parquet` over a DuckLake directory is stale, broken or duplicated —
never merely incomplete. That is worth stating plainly rather than discovering:
the files are portable, the *table* is not, and `ducklake_list_files()` is the
supported way to hand a consumer the current set.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

from .db import row, scalar

# The connection attaches both databases under fixed aliases rather than working
# *from* either of them. A DuckDB catalog is named after its file stem, so
# reading `warehouse.raw.…` would break the moment `WAREHOUSE_PATH` pointed at a
# temp file with another name — which `just test-pipeline` does on every run.
WAREHOUSE_ALIAS = "wh"
LAKEHOUSE_ALIAS = "lakehouse"


@dataclass(frozen=True)
class Synced:
    """One warehouse table to mirror, and what identifies a row in it.

    `key` is the merge key — the columns that say *this is the same row as
    before*, so that a changed value reads as a revision instead of as a new row
    beside the old one. It is the table's grain, and getting it wrong is not a
    crash: too few columns collapses distinct rows into one and too many turns
    every restatement into an insert.

    `provenance_columns` are copied like everything else and **excluded from the
    test for whether a row changed**, which is the difference between a change
    feed and a noise generator. dlt regenerates `_dlt_load_id` *and* `_dlt_id`
    on every row it re-merges, whether or not the values moved — measured, by
    loading one fixture three times and watching both columns change under
    byte-identical weather. Compare them and a routine ingest reports its whole
    merge window as restated.

    The cost of leaving them out is worth stating: the mirrored `_dlt_load_id`
    is then the load that last *changed* the row rather than the one that last
    *touched* it. For an archive of what upstream said, that is the more useful
    of the two, but it is not the same number as the warehouse's.
    """

    table: str
    key: tuple[str, ...]
    provenance_columns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.table.count(".") != 1:
            raise ValueError(f"{self.table!r} is not a schema-qualified table name")
        if not self.key:
            raise ValueError(f"{self.table} needs a merge key — see `Synced.key`")

    @property
    def schema(self) -> str:
        return self.table.split(".")[0]

    @property
    def name(self) -> str:
        return self.table.split(".")[1]


def connect(
    catalog_path: str | Path,
    data_path: str | Path,
    duckdb_path: str | Path,
    data_inlining_row_limit: int,
):
    """An in-memory connection with the warehouse and the lakehouse attached.

    The warehouse is **read-only**: this layer is an archive of it and must never
    be able to write back, the same promise `lake.archive` makes by connecting
    read-only.

    `data_inlining_row_limit` has **no default**, matching `db.write_frames`'s
    `schema` and `export`'s `max_storage_version`. It decides whether a small
    change is written into the catalog database or out to Parquet, which is a
    statement about the artifact rather than a tuning knob — and a default here
    would be invisible to the caller who meant the other one. See the module
    docstring for what each value does to a `read_parquet` over the directory.
    """
    Path(catalog_path).parent.mkdir(parents=True, exist_ok=True)
    Path(data_path).mkdir(parents=True, exist_ok=True)

    con = duckdb.connect()
    con.execute("install ducklake")
    con.execute("load ducklake")
    # ATTACH takes literals, not bind parameters (`attach $path` is a parser
    # error), so the paths are interpolated — as in `history.restore`.
    con.execute(f"attach '{duckdb_path}' as {WAREHOUSE_ALIAS} (read_only)")
    con.execute(
        f"attach 'ducklake:{catalog_path}' as {LAKEHOUSE_ALIAS} "
        f"(data_path '{data_path}/', data_inlining_row_limit {int(data_inlining_row_limit)})"
    )
    return con


def _head(con: duckdb.DuckDBPyConnection) -> int:
    """The newest snapshot in the catalog. Unchanged means nothing was written."""
    return scalar(con, f"select max(snapshot_id) from {LAKEHOUSE_ALIAS}.snapshots()")


def _columns(con: duckdb.DuckDBPyConnection, relation: str) -> list[str]:
    return [name for (name, *_) in con.execute(f"describe {relation}").fetchall()]


def _exists(con: duckdb.DuckDBPyConnection, schema: str, name: str) -> bool:
    return bool(
        con.execute(
            """
            select 1 from duckdb_tables()
            where database_name = $db and schema_name = $schema and table_name = $name
            """,
            {"db": LAKEHOUSE_ALIAS, "schema": schema, "name": name},
        ).fetchall()
    )


def _changes(
    con: duckdb.DuckDBPyConnection, rule: Synced, since: int, until: int
) -> dict[str, int]:
    """The catalog's own account of what the last write did.

    `update_preimage` and `update_postimage` come back as separate rows for one
    revision, so the pair is counted once — a summary saying two updates for one
    changed row would be wrong in the direction people quote.
    """
    counts = dict(
        con.execute(
            f"""
            select change_type, count(*)
            from ducklake_table_changes(
                '{LAKEHOUSE_ALIAS}', '{rule.schema}', '{rule.name}', {since}, {until}
            )
            group by change_type
            """
        ).fetchall()
    )
    return {
        "inserted": counts.get("insert", 0),
        "updated": counts.get("update_postimage", 0),
        "deleted": counts.get("delete", 0),
    }


def _sync_table(con: duckdb.DuckDBPyConnection, rule: Synced) -> dict:
    source = f"{WAREHOUSE_ALIAS}.{rule.schema}.{rule.name}"
    target = f"{LAKEHOUSE_ALIAS}.{rule.schema}.{rule.name}"

    columns = _columns(con, source)
    missing = [c for c in (*rule.key, *rule.provenance_columns) if c not in columns]
    if missing:
        raise ValueError(f"{source} has no column {', '.join(missing)} — check `Synced` for it")

    before = _head(con)
    con.execute(f"create schema if not exists {LAKEHOUSE_ALIAS}.{rule.schema}")

    if not _exists(con, rule.schema, rule.name):
        con.execute(f"create table {target} as select * from {source}")
    else:
        on = " and ".join(f't."{c}" = s."{c}"' for c in rule.key)
        # The columns that decide whether a row was *revised*: everything except
        # the key (equal by the ON clause) and the provenance columns (see
        # `Synced`). A row comparison, because `t.* is distinct from s.*` is a
        # binder error — STAR is not allowed in that position.
        compared = [c for c in columns if c not in rule.key and c not in rule.provenance_columns]
        differs = " or ".join(f'(t."{c}" is distinct from s."{c}")' for c in compared) or "false"

        # `insert by name`, never a positional insert: a column list that has
        # been reordered anywhere upstream loads every value into the wrong
        # column and returns no error, because the types mostly match.
        con.execute(
            f"""
            merge into {target} as t
            using (select * from {source}) as s
            on {on}
            when matched and ({differs}) then update
            when not matched then insert by name
            """
        )
        # The vanished-row half, and the reason it exists at all: `lake.archive`
        # deletes each directory before writing precisely so a row that
        # disappeared upstream cannot keep answering queries. Dropping this
        # would leave the lakehouse with the same defect and no `rmtree` to
        # cover it.
        key_match = " and ".join(f's."{c}" = t."{c}"' for c in rule.key)
        con.execute(
            f"""
            delete from {target} as t
            where not exists (select 1 from {source} as s where {key_match})
            """
        )

    after = _head(con)
    summary = {
        "rows": scalar(con, f"select count(*) from {target}"),
        "snapshot": after if after != before else None,
        **(
            _changes(con, rule, before + 1, after)
            if after != before
            else {"inserted": 0, "updated": 0, "deleted": 0}
        ),
    }
    files, data_bytes = row(
        con,
        f"""
        select count(*), coalesce(sum(data_file_size_bytes), 0)
        from ducklake_list_files('{LAKEHOUSE_ALIAS}', '{rule.name}', schema := '{rule.schema}')
        """,
    )
    return {**summary, "files": files, "bytes": data_bytes}


def sync(
    tables: tuple[Synced, ...],
    duckdb_path: str | Path,
    catalog_path: str | Path,
    data_path: str | Path,
    data_inlining_row_limit: int,
) -> dict[str, dict]:
    """Bring the lakehouse level with the warehouse. Returns a per-table summary.

    `snapshot` is `None` for a table nothing changed in, which is the whole point
    of the layer: a run over unmoved data leaves the catalog exactly as it found
    it, so a snapshot in the list is evidence rather than bookkeeping.
    """
    con = connect(catalog_path, data_path, duckdb_path, data_inlining_row_limit)
    try:
        return {rule.table: _sync_table(con, rule) for rule in tables}
    finally:
        con.close()
