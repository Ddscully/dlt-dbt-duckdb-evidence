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

## The Parquet in a bucket

`LAKEHOUSE_DATA_PATH=s3://bucket/prefix/` puts the data files on S3-compatible
storage. dlt, dbt and every reader here then need the endpoint and keys on each
connection (`storage_secret`).

## The catalog in Postgres

`LAKEHOUSE_CATALOG=postgres://user@host:5432/db` puts DuckLake's own tables in
that database instead of `catalog.duckdb`, under `LAKEHOUSE_METADATA_SCHEMA`.
The two variables are orthogonal: file/disk, file/bucket, Postgres/disk and
Postgres/bucket are all legal, and unset the behaviour is exactly the on-disk
one.

**The URL never carries the password.** libpq reads `PGPASSWORD`, and DuckDB's
postgres extension is libpq — so one variable reaches Python, the DuckDB CLI,
dbt and dlt with no secret in a URL, a rendered profile or the process list.

The release is built from a landing zone on disk — both halves of it — so its
two steps refuse either variable (`refuse_remote_lakehouse`).
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit

import duckdb

from modern_data_stack.ducklake import (
    attach,
    publish as publish_catalog,
    revisions as diff_snapshots,
    row_count,
    set_data_path,
    snapshots,
    sql_literal,
    table_versions,
)
from modern_data_stack.paths import lakehouse_dir as default_lakehouse_dir

# Like WAREHOUSE_PATH, the tests point it at a temp directory.
LAKEHOUSE_DIR = default_lakehouse_dir()

# A DuckDB file beside `data/`, whose Parquet is not the table (`modern_data_stack.ducklake`).
CATALOG_NAME = "catalog.duckdb"
DATA_DIRNAME = "data"

# The bucket (module docstring). DuckDB reads no endpoint from the environment, and
# a signature needs a region that most S3-compatible stores ignore.
DATA_PATH_ENV_VAR = "LAKEHOUSE_DATA_PATH"
S3_ENDPOINT_ENV_VAR = "LAKEHOUSE_S3_ENDPOINT"
DEFAULT_S3_REGION = "us-east-1"

# The catalog name every piece of SQL spells, dbt's `_sources.yml` included.
ATTACH_ALIAS = "lakehouse"

# Postgres (module docstring): both schemes libpq accepts, because dlt renders one.
CATALOG_ENV_VAR = "LAKEHOUSE_CATALOG"
CATALOG_SCHEMES = ("postgres://", "postgresql://")

# The schema holding the `ducklake_*` tables. The default is dlt's too, so both
# sides agree unconfigured; a catalog file's is `main`, spelled explicitly.
METADATA_SCHEMA_ENV_VAR = "LAKEHOUSE_METADATA_SCHEMA"
DEFAULT_METADATA_SCHEMA = ATTACH_ALIAS
FILE_METADATA_SCHEMA = "main"

# A fixture run's own metadata schema, and all `drop_fixture_schema` will delete.
# Lowercase, because Postgres folds the unquoted name the recipe builds in shell.
FIXTURE_SCHEMA_PREFIX = "test_pipeline_"
_FIXTURE_SCHEMA = re.compile(rf"{FIXTURE_SCHEMA_PREFIX}[a-z0-9_]+")

# For reaching past DuckLake into its database; one name cannot be attached twice.
_PROBE_ALIAS = "_catalog_probe"

# The variables that outrank `LAKEHOUSE_DIR`. One tuple, because a variable missing
# from any reader of the list is a fixture run leaking into the real lakehouse.
REMOTE_ENV_VARS = (DATA_PATH_ENV_VAR, CATALOG_ENV_VAR)

# dlt's provenance, rewritten on every re-merge (module docstring); diffs drop it.
DLT_COLUMNS = ("_dlt_load_id", "_dlt_id")

# The one merge-loaded table upstream restates: final ERA5 replaces ERA5T.
WEATHER_TABLE = "raw.om_weather_daily"

# An allowlist the published catalog is built from, never filtered down to: why
# is `tests/test_lakehouse.py`'s failure message.
PUBLISHED_TABLES = ("raw.om_weather_daily",)

__all__ = [
    "ATTACH_ALIAS",
    "CATALOG_ENV_VAR",
    "CATALOG_NAME",
    "CATALOG_SCHEMES",
    "DATA_DIRNAME",
    "DATA_PATH_ENV_VAR",
    "DEFAULT_METADATA_SCHEMA",
    "DEFAULT_S3_REGION",
    "DLT_COLUMNS",
    "FILE_METADATA_SCHEMA",
    "FIXTURE_SCHEMA_PREFIX",
    "LAKEHOUSE_DIR",
    "METADATA_SCHEMA_ENV_VAR",
    "PUBLISHED_TABLES",
    "REMOTE_ENV_VARS",
    "S3_ENDPOINT_ENV_VAR",
    "attach_lakehouse",
    "carried_rows",
    "catalog",
    "catalog_path",
    "data_path",
    "dlt_credentials",
    "drop_fixture_schema",
    "is_catalog",
    "is_remote_catalog",
    "main",
    "metadata_schema",
    "preflight",
    "publish",
    "read_only_connection",
    "refuse_remote_lakehouse",
    "restore",
    "revisions",
    "rows",
    "run",
    "storage_secret",
    "versions",
]


def catalog_path(lakehouse_dir: str | Path = LAKEHOUSE_DIR) -> Path:
    """Where a catalog *file* sits. The file case only — `catalog()` decides."""
    return Path(lakehouse_dir) / CATALOG_NAME


def catalog(lakehouse_dir: str | Path = LAKEHOUSE_DIR) -> str | Path:
    """Where DuckLake keeps its own tables: a file beside the Parquet, or Postgres.

    The mirror of `data_path()`, and it behaves the same way. `LAKEHOUSE_CATALOG`
    is returned as the string it was given, because `Path` collapses
    `postgres://` to `postgres:/` and DuckLake takes the URI verbatim; **the
    variable wins over `lakehouse_dir`**, so a caller pointing at another
    lakehouse must clear it (`just test-pipeline` and the course recipes do, and
    so does the test suite).

    **A password in the URL is refused.** It would reach the process list, dbt's
    rendered profile and dlt's config, none of which redact it — and it is not
    needed, because libpq reads `PGPASSWORD` and DuckDB's postgres extension is
    libpq (measured).
    """
    url = os.environ.get(CATALOG_ENV_VAR)
    if not url:
        return catalog_path(lakehouse_dir)
    if not url.startswith(CATALOG_SCHEMES):
        raise ValueError(
            f"{CATALOG_ENV_VAR}={url!r} is not a {' or '.join(CATALOG_SCHEMES)} URL. It "
            "only moves the DuckLake catalog into Postgres; unset it for a catalog file, "
            "which LAKEHOUSE_DIR places."
        )
    if urlsplit(url).password:
        raise ValueError(
            f"{CATALOG_ENV_VAR} carries a password, which is refused: it would reach the "
            "process list, dbt's rendered profile and dlt's config, and nothing redacts "
            "it there. Put it in PGPASSWORD instead — libpq reads that, and DuckDB's "
            "postgres extension is libpq."
        )
    return url


def is_remote_catalog() -> bool:
    """Whether the catalog is in Postgres rather than a file."""
    return isinstance(catalog(), str)


def metadata_schema() -> str:
    """The schema of the catalog database holding the `ducklake_*` tables.

    `main` for a catalog file. For Postgres, `LAKEHOUSE_METADATA_SCHEMA` or the
    attach alias — and it is what separates one lakehouse from another in a
    single database, which is how `just test-pipeline` isolates a fixture run.
    """
    if not is_remote_catalog():
        return FILE_METADATA_SCHEMA
    return os.environ.get(METADATA_SCHEMA_ENV_VAR) or DEFAULT_METADATA_SCHEMA


def data_path(lakehouse_dir: str | Path = LAKEHOUSE_DIR) -> str | Path:
    """Where the Parquet lives: `data/` beside the catalog, or a bucket.

    A bucket is `LAKEHOUSE_DATA_PATH`, returned as the string it was given:
    `Path` collapses `s3://` to `s3:/`, and dbt reads the same variable, which
    DuckLake compares as a string. **The variable
    wins over `lakehouse_dir`**, so a caller pointing at another catalog must
    clear it: `just test-pipeline` and the course recipes do, and so does the
    test suite.
    """
    url = os.environ.get(DATA_PATH_ENV_VAR)
    if not url:
        return Path(lakehouse_dir) / DATA_DIRNAME
    if not url.startswith("s3://"):
        raise ValueError(
            f"{DATA_PATH_ENV_VAR}={url!r} is not an s3:// URL. It only moves the "
            "Parquet to S3-compatible storage; unset it for a landing zone on disk, "
            "which LAKEHOUSE_DIR places."
        )
    return url


def storage_secret() -> dict[str, str] | None:
    """The S3 secret a connection to a bucket `data_path` needs, or None on disk.

    Needed on *every* connection: DuckDB takes no endpoint from the environment,
    and with no secret it sends the request to AWS, access key id included.
    `attach()` creates this one, `dbt/profiles.yml` spells the same for dbt, and
    `dlt_credentials` hands dlt the parts it builds its own from.
    """
    return _s3_secret() if isinstance(data_path(), str) else None


def _s3_secret() -> dict[str, str]:
    endpoint = _s3_setting(S3_ENDPOINT_ENV_VAR)
    scheme, _, host = endpoint.partition("://")
    if scheme not in ("http", "https") or not host:
        raise ValueError(
            f"{S3_ENDPOINT_ENV_VAR}={endpoint!r} needs its scheme, http:// or https:// — "
            "it decides whether the connection uses TLS."
        )
    return {
        "key_id": _s3_setting("AWS_ACCESS_KEY_ID"),
        "secret": _s3_setting("AWS_SECRET_ACCESS_KEY"),
        "endpoint": host.rstrip("/"),
        "use_ssl": "true" if scheme == "https" else "false",
        "region": os.environ.get("AWS_REGION") or DEFAULT_S3_REGION,
    }


def refuse_remote_lakehouse(step: str) -> None:
    """Stop a release step before it writes anything, if the landing zone is remote.

    The release is built from a landing zone on disk, by decision — a catalog
    file beside its Parquet, which is what `lakehouse.tar.gz` is. Either half
    living elsewhere breaks it, and each breaks it differently.

    With the Parquet in a bucket, the export fails partway
    (measured): its attaches carry no secret, so DuckDB sends the access key id
    to AWS and gets a 403, after leaving a copy of the warehouse — customer ids
    not yet pseudonymised — in the output directory. A restore would unpack local
    Parquet under a catalog that names the bucket.

    With the catalog in Postgres there is no file to publish at all, and a
    restore would unpack one under a catalog that is not a file.
    """
    url = os.environ.get(DATA_PATH_ENV_VAR)
    if url:
        raise RuntimeError(
            f"refusing to {step}: {DATA_PATH_ENV_VAR} puts the landing zone's Parquet "
            f"in {url}, and the release is built from a landing zone on disk. Unset "
            f"it (and point LAKEHOUSE_DIR at a local lakehouse) to {step}."
        )
    catalog_url = os.environ.get(CATALOG_ENV_VAR)
    if catalog_url:
        raise RuntimeError(
            f"refusing to {step}: {CATALOG_ENV_VAR} puts the DuckLake catalog in "
            f"{catalog_url}, and the release publishes a *file* catalog built beside "
            f"the Parquet. Unset it (and point LAKEHOUSE_DIR at a local lakehouse) "
            f"to {step}."
        )


def _s3_setting(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"{DATA_PATH_ENV_VAR} names a bucket, so {name} must be set too (see .env.example)."
        )
    return value


def dlt_credentials(lakehouse_dir: str | Path = LAKEHOUSE_DIR):
    """The destination `ingest.pipeline` loads into.

    Built here rather than in `ingest/` so that the one place that knows where
    the lakehouse *is* is the one place that knows what it is called. dlt takes
    the catalog as a connection string and the storage as a URL; both are
    absolute for the reason in the module docstring.

    dlt attaches through DuckDB, so a Postgres catalog needs nothing of dlt's
    beyond the URL and the schema: the password reaches libpq from `PGPASSWORD`
    as it does everywhere else, and dlt imports no psycopg for this.
    """
    from dlt.destinations.impl.ducklake.configuration import DuckLakeCredentials

    # dlt will not create it. Importing `orchestration.assets` therefore leaves an
    # empty `data/lakehouse/`, which `restore()` tolerates.
    lake = Path(lakehouse_dir)
    lake.mkdir(parents=True, exist_ok=True)
    data = data_path(lake)
    if isinstance(data, Path):
        data.mkdir(parents=True, exist_ok=True)
        storage = f"file://{data}"
    else:
        from dlt.common.configuration.specs import AwsCredentials
        from dlt.common.storages.configuration import FilesystemConfiguration

        # dlt builds its DuckDB secret from these, `http://` turning TLS off. Rebuilt
        # from the secret because dlt strips only the scheme, not a trailing slash.
        secret = _s3_secret()
        scheme = "https" if secret["use_ssl"] == "true" else "http"
        storage = FilesystemConfiguration(
            bucket_url=data,
            credentials=AwsCredentials(
                aws_access_key_id=secret["key_id"],
                aws_secret_access_key=secret["secret"],
                endpoint_url=f"{scheme}://{secret['endpoint']}",
                region_name=secret["region"],
                s3_url_style="path",
            ),
        )
    where = catalog(lake)
    return DuckLakeCredentials(
        ducklake_name=ATTACH_ALIAS,
        catalog=where if isinstance(where, str) else f"duckdb:///{where}",
        # Only for a Postgres catalog: dlt renders no METADATA_SCHEMA without it,
        # and a file catalog's is `main`, which is already DuckLake's default.
        metadata_schema=metadata_schema() if isinstance(where, str) else None,
        storage=storage,
    )


def is_catalog(lakehouse_dir: str | Path = LAKEHOUSE_DIR) -> bool:
    """Whether there is a readable DuckLake catalog here.

    **A file at the path is not enough**, which is not a hypothetical: dbt's
    `ATTACH IF NOT EXISTS` leaves an *empty DuckDB file* at `catalog.duckdb` on
    any build that runs before the first ingest, and a read-only attach of that
    fails with `Existing DuckLake at metadata catalog … does not exist - and
    creating a new DuckLake is explicitly disabled`. So the question is whether
    DuckLake's own metadata table is in it.

    `lakehouse_dir` is ignored when the catalog is in Postgres, where the
    question is whether the metadata *schema* holds that table.
    """
    if is_remote_catalog():
        return _postgres_holds_catalog()
    file = catalog_path(lakehouse_dir)
    if not file.exists():
        return False
    con = duckdb.connect(str(file), read_only=True)
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


def _postgres_holds_catalog() -> bool:
    """Whether the Postgres metadata schema holds DuckLake's metadata table yet.

    **Only an empty schema is False; anything else raises.** The file branch can
    answer "no catalog" from a missing file and be sure of it. Here the same
    answer could mean the host is down, the password is wrong or the database
    does not exist — and `ingest.sources.weather` turns a False into a cold start
    of an archive that costs days of Open-Meteo budget. So the three are kept
    apart: each of them fails the ATTACH with an `IO Error` naming the URL
    (measured), and only a reachable database with nothing in that
    schema returns False.

    The database itself is not created here — `deploy/postgres/init.sql` and the
    operator do that — so a missing one is an error, not an empty lakehouse.
    """
    con = duckdb.connect()
    try:
        con.execute("install postgres")
        con.execute("load postgres")
        # Attached, not `postgres_query`, so the schema name is a bind parameter.
        con.execute(f"attach {sql_literal(catalog())} as {_PROBE_ALIAS} (type postgres, read_only)")
        # Resolves only because DuckDB forwards the name to Postgres; another
        # catalog backend needs another probe.
        return bool(
            con.execute(
                f"""
                select 1 from {_PROBE_ALIAS}.information_schema.tables
                where table_schema = $schema and table_name = 'ducklake_metadata'
                """,
                {"schema": metadata_schema()},
            ).fetchone()
        )
    finally:
        con.close()


def attach_lakehouse(
    con: duckdb.DuckDBPyConnection,
    lakehouse_dir: str | Path = LAKEHOUSE_DIR,
    *,
    read_only: bool = True,
) -> None:
    """Attach the landing zone, wherever its two halves are.

    The one spelling of the ATTACH. The environment picks among the four legal
    combinations, so a caller that spelled its own could open the wrong lakehouse.
    """
    attach(
        con,
        catalog(lakehouse_dir),
        data_path(lakehouse_dir),
        alias=ATTACH_ALIAS,
        read_only=read_only,
        storage_secret=storage_secret(),
        metadata_schema=metadata_schema(),
    )


def read_only_connection(lakehouse_dir: str | Path = LAKEHOUSE_DIR) -> duckdb.DuckDBPyConnection:
    """An in-memory DuckDB with the lakehouse attached read-only.

    Every caller is a reader. Read-only readers can share the catalog with each
    other, never with a writer — the same rule as the warehouse file.
    """
    con = duckdb.connect()
    attach_lakehouse(con, lakehouse_dir, read_only=True)
    return con


def drop_fixture_schema(schema: str) -> None:
    """Drop one `test_pipeline_*` metadata schema from the Postgres catalog.

    The fixture run's equivalent of deleting a `mktemp -d` catalog.

    **The name is checked here, not trusted from the caller**, against a whole
    pattern rather than escaped: the recipe builds it in shell, beside the real
    landing zone's schema, and a pattern leaves nothing to escape on a `cascade`.
    Through the postgres extension, because these machines have no psql.
    """
    if not _FIXTURE_SCHEMA.fullmatch(schema):
        raise ValueError(
            f"refusing to drop schema {schema!r}: only a fixture run's own schema, named "
            f"{FIXTURE_SCHEMA_PREFIX}* and nothing but lowercase, digits and underscores, "
            f"is this function's to delete. The real landing zone's is "
            f"{metadata_schema()!r}."
        )
    con = duckdb.connect()
    try:
        con.execute("install postgres")
        con.execute("load postgres")
        con.execute(f"attach {sql_literal(catalog())} as {_PROBE_ALIAS} (type postgres)")
        # `postgres_execute` and not a DuckDB `drop schema`: the schema holds
        # Postgres tables the scanner does not own and will not cascade.
        con.execute(
            f"call postgres_execute('{_PROBE_ALIAS}', 'drop schema if exists {schema} cascade')"
        )
    finally:
        con.close()


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
        return table_versions(con, ATTACH_ALIAS, table, metadata_schema())
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
    restore. Refuses on a data path in a bucket, on dlt local state and on a
    destination already holding carried rows.
    """
    refuse_remote_lakehouse("restore the landing zone")
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

    Repeats `run()`'s preflight, as a public entry point must, and *before* it
    reads `source_dir` — see the comment below.
    """
    # First: with LAKEHOUSE_CATALOG set, `is_catalog(source)` would ask Postgres
    # about a file catalog and fail with the wrong error, not this refusal.
    preflight(lakehouse_dir)

    source = Path(source_dir)
    if not is_catalog(source):
        raise FileNotFoundError(f"no published lakehouse at {source / CATALOG_NAME}")

    dest = Path(lakehouse_dir)
    if dest.exists():
        # Usually empty (`dlt_credentials`); the preflight refused carried rows.
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
        "one request per indicator and free."
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
    # The only line naming which lakehouse was read, and one Postgres database
    # holds many, told apart by the metadata schema.
    where = catalog()
    if is_remote_catalog():
        where = f"{where} (schema {metadata_schema()})"
    print(f"{where} — {len(snaps)} snapshots, newest {snaps[-1] if snaps else '(none)'}")
    for table, rows in summary["tables"].items():
        print(f"  {table:40} {rows:>10,} rows")


if __name__ == "__main__":
    main()
