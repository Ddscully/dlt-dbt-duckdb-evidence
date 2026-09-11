"""The lakehouse: where dlt lands `raw`, and how to read what changed.

dlt writes straight into this DuckLake catalog, dbt reads `raw` from it, and
`data/warehouse.duckdb` holds only what dbt builds.

Run:  uv run python -m lake.lakehouse       (report the catalog's snapshots)

## Reading what changed

`ducklake_table_changes()` is useless behind dlt: dlt regenerates `_dlt_id` and
`_dlt_load_id` on every row it re-merges, so reloading 500 identical rows reports
500 updates. `revisions()` diffs two snapshots with `EXCEPT` instead, projecting
those columns away — measured at 0 rows for an identical reload and 1 for a
one-row change. It works between any two snapshots and needs no bookkeeping, so
a consumer can re-derive it from the published catalog. The cost is two scans.

## Why the working paths are absolute

A DuckLake catalog stores its `data_path` as given. The working catalog is read
by dlt from the repo root and by dbt from `dbt/`, so its path must be absolute
(`just` exports an absolute `LAKEHOUSE_DIR`). The published one in
`lakehouse.tar.gz` is relative, so a consumer can unpack it anywhere and open it
with a bare `ATTACH`. The measurements are in the `the-lakehouse` skill.
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

# The catalog is a DuckDB file beside `data/`, which holds only Parquet. (Reading
# that Parquet directly is not reading the table — see `modern_data_stack.ducklake`.)
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

# The merge-loaded table with a scheduled upstream restatement (ERA5T is replaced
# by final ERA5 within the 90-day lookback), so the one a revision log is about.
# FX is append-only and retail is frozen.
WEATHER_TABLE = "raw.om_weather_daily"

# What the release publishes out of the landing zone — an allowlist for two
# reasons. Cost: `raw.om_weather_daily` costs more than Open-Meteo's daily budget
# to refetch; everything else in `raw` is free. Disclosure: `raw.retail_invoice_lines`
# and dlt's `raw_staging` copy of it hold clear customer ids, and DuckLake keeps
# dropped tables readable in earlier snapshots (`at (version => …)`), so the
# published catalog is built from this list, never filtered down to it.
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
    "preflight",
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

    # Required: dlt will not create the catalog's parent directory. Because
    # importing `orchestration.assets` builds the pipeline, that import creates an
    # empty `data/lakehouse/` — which `restore()` has to tolerate.
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

    Every caller is a reader. Read-only readers can share the catalog with each
    other, never with a writer — the same rule as the warehouse file.
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

    The landing-zone counterpart of `publish/restore_history.irreplaceable_rows`,
    and separate from it on purpose: that one counts relations inside the DuckDB
    file and this state is not in it. Both the release workflow's "what did we
    carry in" and its "did it shrink" read this, so a table added to
    `PUBLISHED_TABLES` reaches both at once.
    """
    if not is_catalog(lakehouse_dir):
        return 0
    return sum(rows(t, lakehouse_dir) for t in PUBLISHED_TABLES if _has_table(lakehouse_dir, t))


def publish(
    dest_dir: str | Path,
    lakehouse_dir: str | Path = LAKEHOUSE_DIR,
    max_spec_version: str | None = None,
) -> dict[str, int]:
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
            max_spec_version=max_spec_version,
        )
    finally:
        con.close()


def preflight(lakehouse_dir: str | Path = LAKEHOUSE_DIR) -> None:
    """Every reason a restore would refuse, asked before anything is written.

    Separate from `restore` so a caller that writes something else first can ask
    up front: `publish/restore_history.run` replaces `history` before carrying
    the landing zone, and a refusal raised after that would leave a half-applied
    restore. Refuses on dlt local state and on a destination already holding
    carried rows.
    """
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


def restore(source_dir: str | Path, lakehouse_dir: str | Path = LAKEHOUSE_DIR) -> dict[str, int]:
    """Copy a published lakehouse into `lakehouse_dir` before the graph runs.

    A directory copy. Refuses a destination that already holds carried rows —
    there is no force; delete it first to replace it. dlt then merges onto the
    carried rows, because it judges a destination fresh by its own bookkeeping
    tables, which the published catalog does not carry.
    """
    source = Path(source_dir)
    if not is_catalog(source):
        raise FileNotFoundError(f"no published lakehouse at {source / CATALOG_NAME}")

    preflight(lakehouse_dir)

    dest = Path(lakehouse_dir)
    if dest.exists():
        # Usually empty (see `dlt_credentials`); the preflight has already refused
        # one with carried rows. `copytree` needs the path gone.
        shutil.rmtree(dest)

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, dest)
    # The published catalog's `data_path` is relative; a working one must be
    # absolute (see the module docstring). The mirror of `publish`.
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

    With no local state (a fresh runner) dlt finds no `_dlt_version`, treats the
    dataset as new, and merges onto the carried rows. With local state (any
    machine that has run `just ingest`) it trusts what it knows and fails with
    `Table with name _dlt_version does not exist!`. Publishing dlt's bookkeeping
    would not help: dlt would then expect every table its schema describes,
    including the unpublished ones.
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
