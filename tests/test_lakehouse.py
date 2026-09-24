"""The lakehouse: dlt's landing zone, and the revision log derived from it.

dlt writes the catalog directly, so what is left to guard is the substitute for
DuckLake's change feed.

**Why there is a substitute at all** is the finding these tests exist to hold.
`ducklake_table_changes()` is the obvious answer and it does not work behind
dlt: reloading 500 identical rows through `write_disposition="merge"` reports
`update_preimage: 500, update_postimage: 500`, because dlt regenerates `_dlt_id`
*and* `_dlt_load_id` on every row it touches. The feed is faithful and the
writer is what makes it useless. `revisions()` diffs two snapshots with `EXCEPT`
instead, projecting those columns away.

The failure that matters is not an exception. Drop a column from the ignore list
and the diff returns *every* row as revised — a plausible number, in the right
shape, that reads as a catastrophic upstream restatement. So the tests here
assert the zero as hard as they assert the one.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import duckdb
import pytest

from lake import lakehouse
from modern_data_stack.ducklake import attach, revisions, table_versions

WEATHER = "raw.om_weather_daily"

# One day, two capitals. Small enough to read, and two rows is the minimum that
# can distinguish "one row changed" from "everything changed".
DAY = [("DEU", "2021-12-20", 3.5), ("FRA", "2021-12-20", 7.1)]


def _write(lake_dir, loads: list[list[tuple]]) -> None:
    """Write `raw.om_weather_daily` once per entry in `loads`.

    Every write stamps fresh `_dlt_load_id`/`_dlt_id` values, which is what dlt
    does on every merge and the whole reason the diff has to ignore them. Plain
    SQL rather than a dlt run: what is under test is the diff, and a loader in
    the loop would make these tests about dlt's merge instead.
    """
    (lake_dir / "data").mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    attach(con, lake_dir / "catalog.duckdb", lake_dir / "data", alias="lakehouse")
    con.execute("create schema if not exists lakehouse.raw")
    try:
        for n, rows in enumerate(loads):
            values = ", ".join(
                f"('{iso}', date '{day}', {temp}, 'load_{n}', 'id_{n}_{i}')"
                for i, (iso, day, temp) in enumerate(rows)
            )
            con.execute(f"drop table if exists lakehouse.{WEATHER}")
            con.execute(
                f"create table lakehouse.{WEATHER} as select * from (values {values}) as t"
                "(country_iso3, weather_date, temperature_2m_mean, _dlt_load_id, _dlt_id)"
            )
    finally:
        con.close()


def _connect(lake_dir):
    con = duckdb.connect()
    attach(con, lake_dir / "catalog.duckdb", lake_dir / "data", alias="lakehouse", read_only=True)
    return con


def test_an_identical_reload_yields_no_revisions(tmp_path):
    """The routine case, and the one the change feed gets wrong.

    Every ingest re-merges 41 x 90 = 3,690 weather rows whether ERA5 moved or
    not. If that reads as 3,690 revisions the log is noise, which is precisely
    what `ducklake_table_changes()` reports here.
    """
    _write(tmp_path, [DAY, DAY])
    con = _connect(tmp_path)
    try:
        versions = table_versions(con, "lakehouse", WEATHER)
        assert len(versions) >= 2
        changed = revisions(
            con, "lakehouse", WEATHER, versions[-2], versions[-1], ignore=lakehouse.DLT_COLUMNS
        )
    finally:
        con.close()
    assert changed == []


def test_one_restated_value_yields_exactly_that_row(tmp_path):
    _write(tmp_path, [DAY, [("DEU", "2021-12-20", -0.5), ("FRA", "2021-12-20", 7.1)]])
    con = _connect(tmp_path)
    try:
        versions = table_versions(con, "lakehouse", WEATHER)
        changed = revisions(
            con, "lakehouse", WEATHER, versions[-2], versions[-1], ignore=lakehouse.DLT_COLUMNS
        )
    finally:
        con.close()
    assert len(changed) == 1
    assert changed[0][0] == "DEU"
    assert changed[0][2] == -0.5


def test_a_version_whose_snapshot_expired_is_read_at_the_next_surviving_one(tmp_path):
    """Expiry removes snapshots, not live files, so a live file can begin at a
    snapshot that no longer exists. Handed that id, `revisions()` fails with
    `No snapshot found at version N` — what the real catalog did after a
    one-day expiry. Dropping the id instead would end the list at the previous
    change and diff the wrong pair, silently.
    """
    _write(tmp_path, [DAY, [("DEU", "2021-12-20", -0.5), ("FRA", "2021-12-20", 7.1)]])
    con = duckdb.connect()
    attach(con, tmp_path / "catalog.duckdb", tmp_path / "data", alias="lakehouse")
    try:
        restated = table_versions(con, "lakehouse", WEATHER)[-1]
        # A later snapshot, so the one to expire is not the newest.
        con.execute("create table lakehouse.raw.unrelated as select 1 as x")
        con.execute(f"call ducklake_expire_snapshots('lakehouse', versions => [{restated}])")

        versions = table_versions(con, "lakehouse", WEATHER)
        live = {
            row[0]
            for row in con.execute("select snapshot_id from lakehouse.snapshots()").fetchall()
        }
        changed = revisions(
            con, "lakehouse", WEATHER, versions[-2], versions[-1], ignore=lakehouse.DLT_COLUMNS
        )
    finally:
        con.close()
    assert set(versions) <= live
    assert versions[-1] > restated
    assert [(row[0], row[2]) for row in changed] == [("DEU", -0.5)]


# Over DuckLake's inlining limit, so each load writes Parquet that expiry can delete.
LOAD = [(f"C{i:02d}", "2021-12-20", float(i)) for i in range(20)]


def _parquet(lake_dir) -> set:
    return set((lake_dir / "data").rglob("*.parquet"))


def test_expiry_keeps_the_pair_the_weather_check_diffs_and_every_current_row(tmp_path):
    """Expiry deletes files from the only copy of the weather archive, so what
    must survive is asserted whole: the current rows, and the last two loads as
    a diffable pair."""
    restated = [("C00", "2021-12-20", -0.5), *LOAD[1:]]
    _write(tmp_path, [LOAD, LOAD, restated])
    con = _connect(tmp_path)
    try:
        current = sorted(con.execute(f"select * from lakehouse.{WEATHER}").fetchall())
    finally:
        con.close()
    files = _parquet(tmp_path)

    freed = lakehouse.expire(keep=2, lakehouse_dir=tmp_path)

    con = _connect(tmp_path)
    try:
        assert sorted(con.execute(f"select * from lakehouse.{WEATHER}").fetchall()) == current
        versions = table_versions(con, "lakehouse", WEATHER)
        changed = revisions(
            con, "lakehouse", WEATHER, versions[-2], versions[-1], ignore=lakehouse.DLT_COLUMNS
        )
    finally:
        con.close()
    assert len(versions) == 2
    assert [(row[0], row[2]) for row in changed] == [("C00", -0.5)]
    assert freed["snapshots"] > 0
    assert _parquet(tmp_path) < files
    # Nothing older is left to expire, so a second run is a no-op.
    assert lakehouse.expire(keep=2, lakehouse_dir=tmp_path)["snapshots"] == 0


def test_expiry_that_would_leave_no_load_is_refused(tmp_path):
    with pytest.raises(ValueError, match="at least the current load"):
        lakehouse.expire(keep=0, lakehouse_dir=tmp_path)


def test_an_orphan_is_deleted_only_once_it_is_older_than_a_write_could_be(tmp_path):
    """An orphan is Parquet the catalog never recorded — a crashed load's, or
    one still being written. The grace window is what tells them apart."""
    import os
    import time

    from modern_data_stack.ducklake import expire

    _write(tmp_path, [LOAD])
    stale, fresh = tmp_path / "data" / "stale.parquet", tmp_path / "data" / "fresh.parquet"
    for path in (stale, fresh):
        duckdb.sql(f"copy (select 1 as x) to '{path}' (format parquet)")
    two_days_ago = time.time() - 2 * 86_400
    os.utime(stale, (two_days_ago, two_days_ago))

    con = duckdb.connect()
    attach(con, tmp_path / "catalog.duckdb", tmp_path / "data", alias="lakehouse")
    try:
        freed = expire(con, "lakehouse", before=0, delete_orphans=True)
    finally:
        con.close()
    assert freed["orphans"] == 1
    assert not stale.exists()
    assert fresh.exists()


def test_a_single_weather_load_expires_nothing_but_still_sweeps_orphans(tmp_path):
    """The weather pair gates expiring snapshots, not deleting files: a first
    load that crashed, or a catalog with no weather table, would otherwise keep
    its orphans until a second weather load arrived."""
    import os
    import time

    _write(tmp_path, [LOAD])
    orphan = tmp_path / "data" / "crashed.parquet"
    duckdb.sql(f"copy (select 1 as x) to '{orphan}' (format parquet)")
    two_days_ago = time.time() - 2 * 86_400
    os.utime(orphan, (two_days_ago, two_days_ago))

    freed = lakehouse.expire(keep=2, lakehouse_dir=tmp_path)

    assert freed["snapshots"] == 0
    assert freed["orphans"] == 1
    assert not orphan.exists()


def test_expiry_leaves_a_directory_with_no_catalog_alone(tmp_path):
    """Attaching for writes would create an empty catalog there."""
    assert lakehouse.expire(lakehouse_dir=tmp_path)["snapshots"] == 0
    assert not (tmp_path / lakehouse.CATALOG_NAME).exists()


@pytest.mark.parametrize(
    ("where", "deletes_orphans"), [("disk", True), ("s3://bucket/lake/", False)]
)
def test_orphans_are_deleted_only_from_a_data_path_on_disk(
    where, deletes_orphans, monkeypatch, tmp_path
):
    """A bucket prefix can hold what this catalog does not own — a bucket-root
    data path would include every fixture run's `test-pipeline/` prefix — and
    an orphan sweep deletes whatever the catalog does not recognise."""
    spy = MagicMock(return_value={"snapshots": 0, "files": 0, "orphans": 0})
    monkeypatch.setattr(lakehouse, "expire_catalog", spy)
    monkeypatch.setattr(lakehouse, "is_catalog", lambda lakehouse_dir: True)
    monkeypatch.setattr(lakehouse, "attach_lakehouse", MagicMock())
    monkeypatch.setattr(lakehouse, "table_versions", MagicMock(return_value=[1, 2]))
    monkeypatch.setattr(lakehouse, "storage", MagicMock(return_value={}))
    if where != "disk":
        monkeypatch.setenv(lakehouse.DATA_PATH_ENV_VAR, where)

    lakehouse.expire(keep=2, lakehouse_dir=tmp_path)

    assert spy.call_args.kwargs["delete_orphans"] is deletes_orphans
    assert spy.call_args.args[2] == 1


def test_forgetting_the_provenance_columns_reports_the_whole_table(tmp_path):
    """The mutation that proves the ignore list is load-bearing.

    This is the bug the design exists to avoid, run deliberately: compare
    without ignoring anything and an identical reload reports both rows. It
    raises nothing and returns nothing malformed — a wrong answer of the right
    shape.
    """
    _write(tmp_path, [DAY, DAY])
    con = _connect(tmp_path)
    try:
        versions = table_versions(con, "lakehouse", WEATHER)
        unfiltered = revisions(con, "lakehouse", WEATHER, versions[-2], versions[-1], ignore=())
        filtered = revisions(
            con, "lakehouse", WEATHER, versions[-2], versions[-1], ignore=lakehouse.DLT_COLUMNS
        )
    finally:
        con.close()
    assert len(unfiltered) == len(DAY)
    assert filtered == []


def test_ignoring_every_column_is_refused_rather_than_answered(tmp_path):
    """`ignore` covering the whole table would compare nothing and return nothing
    — indistinguishable from "no revisions" and wrong in the safe-looking
    direction. It raises instead."""
    _write(tmp_path, [DAY])
    con = _connect(tmp_path)
    try:
        versions = table_versions(con, "lakehouse", WEATHER)
        all_columns = (
            "country_iso3",
            "weather_date",
            "temperature_2m_mean",
            *lakehouse.DLT_COLUMNS,
        )
        with pytest.raises(ValueError, match="no columns left"):
            revisions(con, "lakehouse", WEATHER, versions[0], None, ignore=all_columns)
    finally:
        con.close()


def test_a_table_the_catalog_does_not_hold_is_named_in_the_error(tmp_path):
    _write(tmp_path, [DAY])
    con = _connect(tmp_path)
    try:
        with pytest.raises(ValueError, match="raw.not_a_table"):
            revisions(con, "lakehouse", "raw.not_a_table", 0, None, ignore=())
    finally:
        con.close()


def test_an_unqualified_table_name_is_refused(tmp_path):
    _write(tmp_path, [DAY])
    con = _connect(tmp_path)
    try:
        with pytest.raises(ValueError, match="schema-qualified"):
            revisions(con, "lakehouse", "om_weather_daily", 0, None, ignore=())
    finally:
        con.close()


def test_the_provenance_list_is_the_one_dlt_actually_writes():
    """`DLT_COLUMNS` here must name every column dlt regenerates, and
    `modern_data_stack.history` already states that set for the carry-forward
    rules. Two hand-written copies of the same fact is how one of them goes
    stale; this holds them together.
    """
    from modern_data_stack.history import DLT_COLUMNS as CARRIED_COLUMNS

    assert lakehouse.DLT_COLUMNS == CARRIED_COLUMNS


def test_the_weather_table_named_here_is_the_one_dlt_loads():
    """`WEATHER_TABLE` is a hand-written copy of a dlt resource name in the
    `raw` dataset. A renamed resource would leave the revision log pointed at a
    table that no longer exists — which raises, but months later and nowhere
    near the rename."""
    from ingest.pipeline import INCREMENTAL_RESOURCES, PIPELINE_DATASET

    schema, name = lakehouse.WEATHER_TABLE.split(".")
    assert schema == PIPELINE_DATASET
    assert name in INCREMENTAL_RESOURCES


def test_the_published_allowlist_is_still_only_the_weather_archive():
    """The project's own value; every mechanism test patches in its fixture's table."""
    assert lakehouse.PUBLISHED_TABLES == ("raw.om_weather_daily",), (
        "the landing tables a release publishes have changed. This is a "
        "disclosure decision: check the new table holds no personal data, and "
        "that it is here for a cost that a rebuild cannot pay — weather is, because "
        "refetching it is days of Open-Meteo's budget. `raw.retail_invoice_lines` "
        "holds clear customer ids, and a release cannot take a table back: DuckLake "
        "keeps it readable at earlier versions (docs/DATA_PROTECTION.md, the "
        "publishing-a-release skill)."
    )


def test_the_attach_alias_is_the_database_dbt_declares():
    """`_sources.yml`'s `database:` and `profiles.yml`'s attach alias are this constant."""
    from pathlib import Path

    import yaml

    why = (
        "dbt resolves every source through this catalog name, so a change to one of "
        "`ATTACH_ALIAS`, `_sources.yml` and `profiles.yml` needs the other two"
    )
    sources = yaml.safe_load(Path("dbt/models/staging/_sources.yml").read_text())
    raw = next(s for s in sources["sources"] if s["name"] == "raw")
    assert raw["database"] == lakehouse.ATTACH_ALIAS, why

    profile = yaml.safe_load(Path("dbt/profiles.yml").read_text())
    attached = profile["modern_data_stack"]["outputs"]["dev"]["attach"]
    assert [a["alias"] for a in attached] == [lakehouse.ATTACH_ALIAS], why


def test_a_bucket_connection_installs_httpfs_before_loading_it(tmp_path):
    """The S3 secret needs httpfs, and a bare `load httpfs` fails on a machine
    that has never downloaded it: `Extension "httpfs" … not found`. The template
    cut from this repo hit exactly that on its first CI run, and
    an empty `HOME` reproduces it here.

    Any machine that has run dbt has httpfs, because the profile lists it, so a
    real attach cannot fail locally, and making a machine without it means a
    download inside a unit test. So this spies on a real connection and holds
    the order of the statements `attach` sends. Attaching an `s3://` data path
    writes nothing to the bucket, so the attach itself runs offline.
    """
    con = duckdb.connect()
    spy = MagicMock(wraps=con)
    try:
        attach(
            spy,
            tmp_path / "catalog.duckdb",
            "s3://lake/prefix/",
            alias="lakehouse",
            storage_secret={
                "key_id": "test",
                "secret": "testtest",
                "endpoint": "127.0.0.1:8333",
                "use_ssl": "false",
                "region": "us-east-1",
            },
        )
    finally:
        con.close()

    statements = [call.args[0].strip().lower() for call in spy.execute.call_args_list]
    assert "load httpfs" in statements, "a bucket attach no longer loads httpfs at all"
    loaded = statements.index("load httpfs")
    assert "install httpfs" in statements[:loaded], (
        "httpfs is loaded without being installed first, which fails on a fresh machine"
    )


# --- The catalog in Postgres -------------------------------------------------
#
# Nothing below connects to a database. The one thing a unit test *can* hold
# about a catalog it cannot reach is the SQL that would be sent, and the rules
# about what may appear in it — which is where the password rule lives.

PG_URL = "postgres://mds@db.example:5432/lakehouse"


def test_a_postgres_catalog_installs_postgres_before_attaching():
    """`load postgres` alone fails on a machine that has never downloaded it.

    The same finding as httpfs above, and it bites harder here: httpfs is in the
    dbt profile, so any machine that has run dbt has it, while `postgres` has no
    such second source. `just extensions` installs all three.

    On a bare mock rather than the `MagicMock(wraps=con)` spy the httpfs test
    uses: that one executes for real, and a real `install postgres` downloads a
    binary from extensions.duckdb.org — inside `just test`, which has no network
    by design. Only the order of the statements is under test, and a mock
    records that exactly.
    """
    con = MagicMock()
    attach(con, PG_URL, "/tmp/lake/data/", alias="lakehouse", metadata_schema="lakehouse")

    statements = [call.args[0].strip().lower() for call in con.execute.call_args_list]
    assert "install postgres" in statements, "a Postgres catalog no longer installs the extension"
    loaded = statements.index("load postgres")
    assert "install postgres" in statements[:loaded], (
        "postgres is loaded without being installed first, which fails on a fresh machine"
    )
    attached = [s for s in statements if s.startswith("attach ")]
    assert len(attached) == 1
    assert statements.index(attached[0]) > loaded, "the attach runs before the extension loads"


def test_the_postgres_attach_carries_the_schema_and_no_password():
    """The URL reaches DuckLake verbatim, and the password is not in it.

    `Path()` would collapse `postgres://` to `postgres:/`, and the metadata
    schema is what separates two lakehouses in one database — so both are
    asserted on the literal, which is the only place they appear.
    """
    con = MagicMock()
    attach(con, PG_URL, "/tmp/lake/data/", alias="lakehouse", metadata_schema="lakehouse")

    statement = next(
        call.args[0] for call in con.execute.call_args_list if call.args[0].startswith("attach ")
    )
    assert statement.startswith(f"attach 'ducklake:postgres:{PG_URL}'")
    assert "metadata_schema 'lakehouse'" in statement
    assert "password" not in statement.lower()


def test_a_file_catalog_is_still_spelled_as_a_duckdb_path(tmp_path):
    """The file case did not move. `main` is spelled out rather than defaulted.

    Measured: DuckLake accepts an explicit `metadata_schema 'main'` on
    a catalog file, which is what lets `attach()` and the dbt profile keep one
    code path instead of branching on the catalog type twice.
    """
    con = MagicMock()
    catalog = tmp_path / "catalog.duckdb"
    attach(con, catalog, tmp_path / "data", alias="lakehouse", metadata_schema="main")

    statement = next(
        call.args[0] for call in con.execute.call_args_list if call.args[0].startswith("attach ")
    )
    assert statement.startswith(f"attach 'ducklake:duckdb:{catalog}'")
    assert "metadata_schema 'main'" in statement
    assert "install postgres" not in [c.args[0] for c in con.execute.call_args_list]


def test_the_catalog_is_a_path_until_the_variable_names_a_database(tmp_path, monkeypatch):
    assert lakehouse.catalog(tmp_path) == tmp_path / lakehouse.CATALOG_NAME
    assert lakehouse.is_remote_catalog() is False
    assert lakehouse.metadata_schema() == lakehouse.FILE_METADATA_SCHEMA

    monkeypatch.setenv(lakehouse.CATALOG_ENV_VAR, PG_URL)
    # The variable outranks the directory, exactly as LAKEHOUSE_DATA_PATH does.
    assert lakehouse.catalog(tmp_path) == PG_URL
    assert lakehouse.is_remote_catalog() is True
    assert lakehouse.metadata_schema() == lakehouse.DEFAULT_METADATA_SCHEMA

    monkeypatch.setenv(lakehouse.METADATA_SCHEMA_ENV_VAR, "somewhere_else")
    assert lakehouse.metadata_schema() == "somewhere_else"


def test_a_password_in_the_catalog_url_is_refused_and_names_pgpassword(monkeypatch):
    """The rule the whole design rests on, and the only place it can be enforced.

    A password in the URL would reach the process list, dbt's rendered profile
    and dlt's config, none of which redact it. It is also unnecessary: libpq
    reads PGPASSWORD, and DuckDB's postgres extension is libpq (measured:
    unset, the attach fails with `fe_sendauth: no password
    supplied`).
    """
    monkeypatch.setenv(
        lakehouse.CATALOG_ENV_VAR, "postgres://mds:hunter2@db.example:5432/lakehouse"
    )
    with pytest.raises(ValueError, match="PGPASSWORD"):
        lakehouse.catalog()


def test_a_catalog_url_that_is_not_postgres_is_refused(monkeypatch):
    monkeypatch.setenv(lakehouse.CATALOG_ENV_VAR, "mysql://mds@db.example:3306/lakehouse")
    with pytest.raises(ValueError, match=lakehouse.CATALOG_ENV_VAR):
        lakehouse.catalog()


@pytest.mark.parametrize("variable", lakehouse.REMOTE_ENV_VARS)
def test_the_release_refuses_a_landing_zone_that_is_not_on_disk(variable, monkeypatch):
    """Both halves, each named in its own message.

    The release publishes a catalog file beside its Parquet. Either half living
    elsewhere breaks it, and a refusal that named only one would let the other
    through — which is how the export came to leave an unpseudonymised copy of
    the warehouse behind before it failed.
    """
    monkeypatch.setenv(variable, "postgres://mds@db.example:5432/lakehouse")
    with pytest.raises(RuntimeError, match=variable):
        lakehouse.refuse_remote_lakehouse("export")


def test_the_remote_variables_are_the_ones_that_outrank_lakehouse_dir(monkeypatch, tmp_path):
    """The tuple is not a list of names, it is the answer to one question.

    Four places read it — the release's refusal, `tests/conftest.py`, the course
    recipes' guard in `tests/test_workflows.py` and the docs — so a variable that
    redirects part of the landing zone and is missing here is missing from all of
    them at once.
    """
    for variable in lakehouse.REMOTE_ENV_VARS:
        monkeypatch.delenv(variable, raising=False)
    assert lakehouse.data_path(tmp_path) == tmp_path / lakehouse.DATA_DIRNAME
    assert lakehouse.catalog(tmp_path) == tmp_path / lakehouse.CATALOG_NAME

    monkeypatch.setenv(lakehouse.DATA_PATH_ENV_VAR, "s3://bucket/prefix/")
    monkeypatch.setenv(lakehouse.CATALOG_ENV_VAR, PG_URL)
    assert lakehouse.data_path(tmp_path) == "s3://bucket/prefix/"
    assert lakehouse.catalog(tmp_path) == PG_URL


@pytest.mark.parametrize(
    "schema",
    [
        "lakehouse",
        "",
        "public",
        "test_pipeline",
        "TEST_PIPELINE_X",
        "a; drop schema b",
        # Past the prefix, and still not this function's to delete. A prefix
        # check passes all three: the first two would reach the statement as
        # written, and the third is why the name is matched rather than escaped.
        "test_pipeline_TMP",
        "test_pipeline_a b",
        "test_pipeline_x'; drop schema lakehouse cascade; --",
    ],
)
def test_dropping_anything_but_a_fixture_schema_is_refused(schema, monkeypatch):
    """The guard on a `cascade` that cannot be undone.

    `just test-pipeline` builds the name in shell, so the failure to defend
    against is a name that arrives empty or unexpanded — next to which sits the
    real landing zone's schema. Checked as a whole pattern rather than a prefix,
    which is also what leaves nothing to escape in the statement.
    """
    monkeypatch.setenv(lakehouse.CATALOG_ENV_VAR, PG_URL)
    with pytest.raises(ValueError, match=lakehouse.FIXTURE_SCHEMA_PREFIX):
        lakehouse.drop_fixture_schema(schema)


def test_dlt_is_given_the_database_and_the_schema_with_no_password(monkeypatch, tmp_path):
    """dlt attaches through DuckDB, so it needs the URL and nothing else.

    The schema is passed only for a Postgres catalog: a file catalog's is `main`,
    which is DuckLake's own default, and dlt renders no option for None.
    """
    monkeypatch.setenv(lakehouse.CATALOG_ENV_VAR, PG_URL)
    credentials = lakehouse.dlt_credentials(tmp_path)
    assert credentials.catalog.drivername == "postgres"
    assert credentials.catalog.password is None
    assert credentials.metadata_schema == lakehouse.DEFAULT_METADATA_SCHEMA

    monkeypatch.delenv(lakehouse.CATALOG_ENV_VAR)
    on_disk = lakehouse.dlt_credentials(tmp_path)
    assert on_disk.catalog.drivername == "duckdb"
    assert on_disk.metadata_schema is None


def test_the_profile_spells_the_postgres_catalog_the_way_the_code_does():
    """dbt reaches the same lakehouse, or the graph quietly splits in two.

    The counterpart of the attach-alias test above: that one holds *which*
    database, this one holds *where* it is. Both defaults are read from the
    constants rather than retyped, since a profile that disagreed by one word
    would build `staging` against an empty catalog and go green.
    """
    from pathlib import Path

    import yaml

    profile = yaml.safe_load(Path("dbt/profiles.yml").read_text())
    output = profile["modern_data_stack"]["outputs"]["dev"]
    assert "postgres" in output["extensions"], (
        "the profile no longer installs the postgres extension, so a catalog in "
        "Postgres fails the attach on a machine that has never downloaded it"
    )

    (attached,) = output["attach"]
    assert lakehouse.CATALOG_ENV_VAR in attached["path"]
    assert "ducklake:postgres:" in attached["path"]

    schema = attached["options"]["metadata_schema"]
    assert lakehouse.METADATA_SCHEMA_ENV_VAR in schema
    assert f"'{lakehouse.DEFAULT_METADATA_SCHEMA}'" in schema, (
        "the profile's default metadata schema no longer matches the code's, so dbt "
        "and dlt would write DuckLake's tables into two different schemas"
    )
    assert f"'{lakehouse.FILE_METADATA_SCHEMA}'" in schema, (
        "the profile no longer falls back to the file catalog's schema"
    )


def test_the_metadata_schema_reaches_the_query_and_is_not_assumed(tmp_path):
    """`table_versions` reads the catalog database, and `main` is not always there.

    Under a Postgres catalog the `ducklake_*` tables sit in the metadata schema
    and `main` does not exist at all, so an unqualified read fails with
    `schema "main" does not exist` (measured). No unit test can reach
    a Postgres catalog, but pointing the file case at a schema that is not there
    proves the argument is what the SQL is built from — which is the half that
    silently returned nothing when it was missing.
    """
    _write(tmp_path, [DAY])
    con = _connect(tmp_path)
    try:
        assert table_versions(con, "lakehouse", WEATHER, "main")
        with pytest.raises(duckdb.Error):
            table_versions(con, "lakehouse", WEATHER, "not_a_schema")
    finally:
        con.close()


def test_every_reader_here_asks_where_the_metadata_schema_is(tmp_path, monkeypatch):
    """`versions()` passes it on rather than taking the default.

    The default is the file case, so leaving the argument off is invisible until
    the catalog is in Postgres — where every version list comes back empty and
    the revision log silently reports nothing changed.
    """
    seen = {}

    def spy(con, alias, table, metadata_schema="main"):
        seen["metadata_schema"] = metadata_schema
        return []

    monkeypatch.setattr(lakehouse, "table_versions", spy)
    monkeypatch.setenv(lakehouse.CATALOG_ENV_VAR, PG_URL)
    monkeypatch.setattr(lakehouse, "read_only_connection", lambda _: MagicMock())

    lakehouse.versions(WEATHER, tmp_path)
    assert seen["metadata_schema"] == lakehouse.DEFAULT_METADATA_SCHEMA
