"""The lakehouse: where dlt lands `raw`, and how to read what changed.

This used to be a *mirror* — dlt wrote `raw` into `data/warehouse.duckdb` and a
gated `MERGE` copied two weather tables into a DuckLake catalog beside it, so
the format could demonstrate a row-level change feed. It is now the landing zone
itself: dlt writes straight into DuckLake, dbt reads `raw` from here, and the
DuckDB file holds only what dbt builds. The hive archive that used to sit under
`data/lake/` is gone with it.

Run:  uv run python -m lake.lakehouse       (report the catalog's snapshots)

## What moved, and the one thing it cost

The gated `MERGE` is gone, and with it the clean `ducklake_table_changes()`
feed. **dlt's own merge cannot produce one**, which is measured rather than
assumed: loading 500 identical rows through `write_disposition="merge"` a second
time reports `update_preimage: 500, update_postimage: 500`. dlt regenerates
`_dlt_id` *and* `_dlt_load_id` on every row it re-merges, so every row genuinely
differs and DuckLake is right to say so. A feed that reports a no-op reload and
a one-row restatement identically is not a feed.

What replaces it is `revisions()` below: diff two snapshots with `EXCEPT`,
projecting away dlt's provenance columns. Measured on the same three loads —
**0 rows** for the identical reload, **1 row** for the one-row change, naming the
row. Two properties make this the better trade rather than a consolation:

* it works between **any** two snapshots, not only adjacent ones, so "what
  changed since last month's release" is one query rather than a fold over
  every intervening load;
* it is a query a consumer can re-derive from the published catalog months
  later, with no bookkeeping this repo has to keep correct.

The cost is two full scans instead of a change-log read. At 219,350 weather rows
that is milliseconds, and the ratio only improves as the table grows, because
the scans parallelise and the change log does not.

## Why the working paths are absolute

A DuckLake catalog stores its `data_path` as given: pass a relative one and the
catalog is relocatable with a bare `ATTACH`, pass an absolute one and a consumer
needs `OVERRIDE_DATA_PATH`. Relative would therefore be right for a *published*
lakehouse — and this one is not published, because the release ships the curated
DuckDB file alone. It is read by dlt from the repo root and by dbt from `dbt/`,
two different working directories, which a relative path cannot serve. So these
are absolute on purpose, and `PUBLISHING.md`'s note is where the other choice
belongs if that ever changes.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import duckdb

from modern_data_stack.ducklake import (
    attach,
    publish as publish_catalog,
    revisions as diff_snapshots,
    row_count,
    set_data_path,
    snapshots,
    table_versions,
)
from modern_data_stack.paths import lakehouse_dir as default_lakehouse_dir

# LAKEHOUSE_DIR mirrors WAREHOUSE_PATH: the tests point it at a temp directory so
# a fixture run cannot write over the real catalog.
LAKEHOUSE_DIR = default_lakehouse_dir()

# The catalog is a DuckDB file beside the data rather than inside it, so `data/`
# holds nothing but Parquet. That is what lets somebody read the files without
# the catalog — a weaker guarantee than the hive archive gave (see
# `modern_data_stack.ducklake` on why `read_parquet` is not the table), but the
# layout is still the one a reader expects to find.
CATALOG_NAME = "catalog.duckdb"
DATA_DIRNAME = "data"

# The ATTACH name, and therefore the catalog every piece of SQL in the project
# spells out. dbt's `_sources.yml` says `database: lakehouse`; changing this
# without changing that splits the graph exactly the way a renamed dlt resource
# does.
ATTACH_ALIAS = "lakehouse"

# dlt's per-row provenance, regenerated on every re-merge whether the data moved
# or not — see the module docstring. Every comparison here projects them away.
DLT_COLUMNS = ("_dlt_load_id", "_dlt_id")

# The one table in the catalog that restates, and so the only one a revision log
# says anything about. FX is append-only, retail is frozen at 2011-12, and
# `raw.owid_co2` has produced zero observed revisions locally — weather's 90-day
# ERA5T lookback re-merges 41 x 90 = 3,690 rows on a *scheduled* upstream
# property. Named here rather than in `orchestration/` because it is a fact about
# the data, and the asset check is only one of its readers.
WEATHER_TABLE = "raw.om_weather_daily"

# What the release publishes out of the landing zone, and it is an allowlist for
# two independent reasons.
#
# **Cost.** `raw.om_weather_daily` is unreproducible within a *budget* — the
# archive costs more than Open-Meteo's 10,000 units a day — so a release that did
# not carry it would cold-start the next one at `WEATHER_COLD_START_YEARS`
# forever. Everything else in `raw` is a free re-fetch.
#
# **Disclosure.** `raw.retail_invoice_lines` and dlt's `raw_staging` copy of it
# hold 824,364 clear customer ids between them. Shipping `raw` whole would undo
# the single largest privacy gain of moving the landing zone out of the published
# file — and **it could not be fixed after the fact**: DuckLake keeps dropped
# tables in earlier snapshots, so `at (version => …)` still returns them. Verified
# by dropping a table and reading a customer id back out of the version before it.
# So the published catalog is *built* from this list, never filtered down to it.
PUBLISHED_TABLES = ("raw.om_weather_daily",)

__all__ = [
    "ATTACH_ALIAS",
    "CATALOG_NAME",
    "DATA_DIRNAME",
    "DLT_COLUMNS",
    "LAKEHOUSE_DIR",
    "PUBLISHED_TABLES",
    "carried_rows",
    "catalog_path",
    "data_path",
    "dlt_credentials",
    "is_catalog",
    "main",
    "publish",
    "read_only_connection",
    "restore",
    "revisions",
    "rows",
    "run",
    "versions",
]


def catalog_path(lakehouse_dir: str | Path = LAKEHOUSE_DIR) -> Path:
    return Path(lakehouse_dir) / CATALOG_NAME


def data_path(lakehouse_dir: str | Path = LAKEHOUSE_DIR) -> Path:
    return Path(lakehouse_dir) / DATA_DIRNAME


def dlt_credentials(lakehouse_dir: str | Path = LAKEHOUSE_DIR):
    """The destination `ingest.pipeline` loads into.

    Built here rather than in `ingest/` so that the one place that knows where
    the lakehouse *is* is the one place that knows what it is called. dlt takes
    the catalog as a connection string and the storage as a URL; both are
    absolute for the reason in the module docstring.
    """
    from dlt.destinations.impl.ducklake.configuration import DuckLakeCredentials

    # **The mkdir is required and it is an import-time side effect**, which is an
    # unpleasant pair worth naming. dlt will not create the catalog file's parent
    # (`Cannot open file …/catalog.duckdb: No such file or directory`), and
    # `build_pipeline()` calls this, and importing `orchestration.assets` calls
    # `build_pipeline()` at import time — so merely importing the orchestration
    # layer creates an empty `data/lakehouse/`. It is gitignored and harmless
    # except in one place: `restore()` below has to tolerate finding it, because
    # `shutil.copytree` refuses an existing destination.
    lake = Path(lakehouse_dir)
    lake.mkdir(parents=True, exist_ok=True)
    data_path(lake).mkdir(parents=True, exist_ok=True)
    return DuckLakeCredentials(
        ducklake_name=ATTACH_ALIAS,
        catalog=f"duckdb:///{catalog_path(lake)}",
        storage=f"file://{data_path(lake)}",
    )


def is_catalog(lakehouse_dir: str | Path = LAKEHOUSE_DIR) -> bool:
    """Whether there is a readable DuckLake catalog here.

    **A file at the path is not enough**, which is not a hypothetical: dbt's
    `ATTACH IF NOT EXISTS` leaves an *empty DuckDB file* at `catalog.duckdb` on
    any build that runs before the first ingest, and a read-only attach of that
    fails with `Existing DuckLake at metadata catalog … does not exist - and
    creating a new DuckLake is explicitly disabled`. So the question is whether
    DuckLake's own metadata table is in it.
    """
    catalog = catalog_path(lakehouse_dir)
    if not catalog.exists():
        return False
    con = duckdb.connect(str(catalog), read_only=True)
    try:
        return bool(
            con.execute(
                "select 1 from duckdb_tables() where table_name = 'ducklake_metadata'"
            ).fetchone()
        )
    except duckdb.Error:
        return False
    finally:
        con.close()


def read_only_connection(lakehouse_dir: str | Path = LAKEHOUSE_DIR) -> duckdb.DuckDBPyConnection:
    """An in-memory DuckDB with the lakehouse attached read-only.

    Read-only because every caller of this is a *reader* — the observability
    tables, the asset checks, `revisions()` — and DuckLake takes one writer at a
    time exactly as DuckDB does. A reader that opens it writable is the lock
    contention this project already documents for the warehouse file.
    """
    con = duckdb.connect()
    attach(
        con,
        catalog_path(lakehouse_dir),
        data_path(lakehouse_dir),
        alias=ATTACH_ALIAS,
        read_only=True,
    )
    return con


def revisions(
    table: str,
    since: int,
    until: int | None = None,
    lakehouse_dir: str | Path = LAKEHOUSE_DIR,
) -> list[tuple]:
    """Rows of `table` that genuinely differ between two snapshots.

    `table` is schema-qualified (`raw.om_weather_daily`). Provenance columns are
    projected away, which is the whole point — see the module docstring.
    """
    con = read_only_connection(lakehouse_dir)
    try:
        return diff_snapshots(con, ATTACH_ALIAS, table, since, until, ignore=DLT_COLUMNS)
    finally:
        con.close()


def versions(table: str, lakehouse_dir: str | Path = LAKEHOUSE_DIR) -> list[int]:
    """Snapshots in which `table` changed, oldest first — the diffable points."""
    con = read_only_connection(lakehouse_dir)
    try:
        return table_versions(con, ATTACH_ALIAS, table)
    finally:
        con.close()


def rows(table: str, lakehouse_dir: str | Path = LAKEHOUSE_DIR) -> int:
    con = read_only_connection(lakehouse_dir)
    try:
        return row_count(con, ATTACH_ALIAS, table)
    finally:
        con.close()


def carried_rows(lakehouse_dir: str | Path = LAKEHOUSE_DIR) -> int:
    """Rows here that a rebuild cannot afford to fetch again.

    The landing-zone counterpart of `scripts/restore_history.irreplaceable_rows`,
    and separate from it on purpose: that one counts relations inside the DuckDB
    file and this state is not in it. Both the release workflow's "what did we
    carry in" and its "did it shrink" read this, so a table added to
    `PUBLISHED_TABLES` reaches both at once.
    """
    if not is_catalog(lakehouse_dir):
        return 0
    return sum(rows(t, lakehouse_dir) for t in PUBLISHED_TABLES if _has_table(lakehouse_dir, t))


def publish(dest_dir: str | Path, lakehouse_dir: str | Path = LAKEHOUSE_DIR) -> dict[str, int]:
    """Write the publishable subset of the landing zone to `dest_dir`.

    Relocatable, so a consumer opens it with a bare `ATTACH` from the directory
    they unpacked it into — and so the *next* release can restore it without
    knowing where this one built it.
    """
    con = read_only_connection(lakehouse_dir)
    try:
        return publish_catalog(
            con,
            ATTACH_ALIAS,
            dest_dir,
            PUBLISHED_TABLES,
            data_dirname=DATA_DIRNAME,
            catalog_name=CATALOG_NAME,
        )
    finally:
        con.close()


def restore(source_dir: str | Path, lakehouse_dir: str | Path = LAKEHOUSE_DIR) -> dict[str, int]:
    """Copy a published lakehouse into `lakehouse_dir` before the graph runs.

    The landing-zone analogue of `scripts/restore_history.py`, and simpler than
    it: a DuckLake is a directory, so this is a copy rather than a schema-aware
    `create or replace`. It **refuses a destination that already holds rows**, for
    the reason that module states — the weather archive is no easier to get back
    than a revision is, and `--force` is how you say you meant it.

    dlt then merges onto the carried rows. That works for the reason the carried
    *table* worked before: dlt keys "is this destination fresh?" on its own
    bookkeeping, and a catalog holding only `raw.om_weather_daily` has none of it.
    """
    source = Path(source_dir)
    if not is_catalog(source):
        raise FileNotFoundError(f"no published lakehouse at {source / CATALOG_NAME}")

    state = _local_pipeline_state()
    if state is not None:
        _refuse_warm_state(state)

    dest = Path(lakehouse_dir)
    if is_catalog(dest):
        held = sum(rows(t, dest) for t in PUBLISHED_TABLES if _has_table(dest, t))
        if held:
            raise ValueError(
                f"{catalog_path(dest)} already holds {held:,} rows in {len(PUBLISHED_TABLES)} "
                "carried table(s) — refusing to overwrite an archive that costs days of "
                "API budget to refetch. Delete it first if replacing it is really what "
                "you want."
            )
    if dest.exists():
        # An empty directory is the normal state here, not an anomaly: importing
        # the orchestration layer creates one (see `dlt_credentials`). Only a
        # catalog with carried rows in it stops the restore, and that is checked
        # above — this just clears the way for `copytree`.
        shutil.rmtree(dest)

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, dest)
    # The published catalog carries a *relative* `data_path` so a consumer can
    # open it with a bare ATTACH. A working one cannot: dlt reads it from the
    # repo root and dbt from `dbt/`. DuckLake checks the two agree and refuses
    # otherwise, so the form is put back here — the exact mirror of `publish`.
    set_data_path(catalog_path(dest), f"{data_path(dest)}/")
    return {t: rows(t, dest) for t in PUBLISHED_TABLES if _has_table(dest, t)}


def _local_pipeline_state() -> Path | None:
    """dlt's state directory for this project's pipeline, if it has one.

    Read rather than built: constructing the pipeline to ask its name is what
    would create the state this is looking for.
    """
    from dlt.common.pipeline import get_dlt_pipelines_dir

    from ingest.pipeline import pipeline_name

    state = Path(get_dlt_pipelines_dir()) / pipeline_name()
    return state if state.exists() else None


def _refuse_warm_state(state: Path) -> None:
    """Stop before a restore that dlt's local state would make fail.

    This guard moved here with the landing zone; it used to live in
    `scripts/restore_history.py`, back when a *table* was carried into the
    warehouse's `raw` schema. The mechanism changed completely — a directory copy
    rather than a `create or replace` — and the failure did not change at all,
    which is worth knowing before assuming a new implementation escapes an old
    trap. Re-measured against the DuckLake restore rather than inherited:

    * **No local state** (a fresh runner — `release-data.yml`, `pages.yml`,
      `nightly.yml`) — dlt queries the destination, finds no `_dlt_version`,
      concludes the dataset is new and creates its bookkeeping. The carried rows
      survive and the merge lands on them. Measured: 44,936 weather rows through
      a full fixture load.
    * **Local state** (any machine that has run `just ingest`) — dlt trusts what
      it already knows and dies with `Table with name _dlt_version does not
      exist!`. Measured on the restored catalog, same message as before.

    Carrying dlt's own bookkeeping along does not help and is why
    `PUBLISHED_TABLES` names data tables only: dlt would then believe every table
    its schema describes is present, and the resources that were never published
    fail on the ones that are missing.
    """
    raise RuntimeError(
        f"dlt has local pipeline state at {state}, and this restore replaces the "
        "landing zone that state describes. dlt would look for bookkeeping the "
        "restored catalog does not have and fail with `Table with name "
        "_dlt_version does not exist!`. Drop the state first:\n"
        f"    rm -rf {state}\n"
        "The next load re-fetches what it was tracking (WDI's watermark), which is "
        "eleven requests and free."
    )


def _has_table(lakehouse_dir: str | Path, table: str) -> bool:
    schema, name = table.split(".")
    con = read_only_connection(lakehouse_dir)
    try:
        return bool(
            con.execute(
                """
                select 1 from information_schema.tables
                where table_catalog = $catalog and table_schema = $schema and table_name = $table
                """,
                {"catalog": ATTACH_ALIAS, "schema": schema, "table": name},
            ).fetchone()
        )
    finally:
        con.close()


def run(lakehouse_dir: str | Path = LAKEHOUSE_DIR) -> dict:
    """What the catalog currently holds: tables, rows and snapshot lineage."""
    con = read_only_connection(lakehouse_dir)
    try:
        tables = con.execute(
            f"""
            select table_schema, table_name
            from information_schema.tables
            where table_catalog = '{ATTACH_ALIAS}' and table_type = 'BASE TABLE'
            order by 1, 2
            """
        ).fetchall()
        counts = {
            f"{schema}.{name}": row_count(con, ATTACH_ALIAS, f"{schema}.{name}")
            for schema, name in tables
        }
        return {"tables": counts, "snapshots": snapshots(con, ATTACH_ALIAS)}
    finally:
        con.close()


def main() -> None:
    summary = run()
    snaps = summary["snapshots"]
    print(f"{catalog_path()} — {len(snaps)} snapshots, newest {snaps[-1] if snaps else '(none)'}")
    for table, rows in summary["tables"].items():
        print(f"  {table:40} {rows:>10,} rows")


if __name__ == "__main__":
    main()
