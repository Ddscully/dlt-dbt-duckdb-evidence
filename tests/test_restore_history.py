"""Unit tests for the release-to-release history carry-forward
(`scripts/restore_history.py`).

Miniature DuckDB files in a tmp dir, like `test_export.py` — what matters here is
the contract, not the numbers: history lands in the destination, an empty source
is a no-op rather than an error, and a destination that already holds history is
never silently overwritten.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from lake import lakehouse
from modern_data_stack import history
from modern_data_stack.db import scalar
from modern_data_stack.history import DLT_COLUMNS, SCD2_COLUMNS, Carry
from scripts import restore_history
from scripts.restore_history import irreplaceable_rows, run

# Captured before the autouse fixture below replaces it, so the one test that
# exercises the real lookup can still reach it.
_real_state_lookup = lakehouse._local_pipeline_state


@pytest.fixture(autouse=True)
def no_local_dlt_state(monkeypatch):
    """Every test here runs as though dlt has no local state.

    Without this the suite passes on CI and fails on any laptop that has run
    `just ingest`, because the carry-forward refuses a landing table when dlt
    has state — which is the whole point of the refusal, and exactly the kind of
    environment dependency that makes a test worthless. dlt resolves its global
    directory once per process, so `DLT_DATA_DIR` cannot be set after import;
    the lookup itself is what gets replaced. Tests about the refusal override
    this deliberately.
    """
    monkeypatch.setattr(lakehouse, "_local_pipeline_state", lambda: None)


# One country-year at version 1, shaped the way dbt writes a `check`-strategy
# snapshot. The four dbt_* columns are what the script requires.
SNAPSHOT = """
create schema history;
create table history.snap_co2_estimates as
select * from (values
    ('DEU-2019', 'DEU', 2019, 700.0, 8.4, 'a1', now(), now(), null),
    ('FRA-2019', 'FRA', 2019, 300.0, 4.5, 'b2', now(), now(), null)
) t(country_year, country_iso3, year, co2_mt, co2_per_capita,
    dbt_scd_id, dbt_updated_at, dbt_valid_from, dbt_valid_to);
"""

# The other half of what a release carries: a rate-limited landing table, plus
# the dlt bookkeeping and the sibling landing table that must *not* come with it.
# `_dlt_loads` is the one that matters — dlt reads it to decide whether the
# destination is fresh, so carrying it would skip the next load entirely.
WEATHER = """
create schema raw;
create table raw.om_weather_daily as
select * from (values
    ('DEU', date '2024-01-01', 3.2, 'load-1', 'row-a'),
    ('FRA', date '2024-01-01', 6.8, 'load-1', 'row-b')
) t(country_iso3, weather_date, temperature_2m_mean, _dlt_load_id, _dlt_id);
create table raw.owid_co2 as select 1 as year, 'load-1' as _dlt_load_id, 'r' as _dlt_id;
create table raw._dlt_loads as select 'load-1' as load_id;
create table raw._dlt_pipeline_state as select 1 as version;
"""


def _db(path: Path, setup: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    if setup:
        con.execute(setup)
    con.close()
    return path


@pytest.fixture
def previous(tmp_path: Path) -> Path:
    """Stands in for the warehouse.duckdb downloaded off the last release."""
    return _db(tmp_path / "prev" / "warehouse.duckdb", SNAPSHOT)


def _history_rows(path: Path) -> int:
    con = duckdb.connect(str(path), read_only=True)
    try:
        return scalar(con, "select count(*) from history.snap_co2_estimates")
    finally:
        con.close()


def test_restores_into_a_warehouse_that_does_not_exist_yet(previous: Path, tmp_path: Path):
    """The workflow order: restore first, then let dlt create `raw` in the same
    file. So the destination legitimately isn't there yet."""
    dest = tmp_path / "new" / "warehouse.duckdb"

    summary = run(previous, dest)

    assert summary["rows"] == 2
    assert summary["tables"] == [{"table": "history.snap_co2_estimates", "rows": 2}]
    assert _history_rows(dest) == 2


def test_leaves_the_rest_of_the_destination_alone(previous: Path, tmp_path: Path):
    dest = _db(
        tmp_path / "wh" / "warehouse.duckdb",
        "create schema raw; create table raw.t as select 1 as a;",
    )

    run(previous, dest)

    con = duckdb.connect(str(dest), read_only=True)
    try:
        assert scalar(con, "select count(*) from raw.t") == 1
    finally:
        con.close()


def test_a_source_without_history_is_a_no_op(tmp_path: Path):
    """The first release ever cut has no predecessor, and neither does one from
    before the snapshot existed. Neither is an error."""
    source = _db(tmp_path / "prev" / "warehouse.duckdb", "create schema marts;")
    dest = tmp_path / "wh" / "warehouse.duckdb"

    summary = run(source, dest)

    assert summary["tables"] == []
    assert summary["rows"] == 0


def test_an_empty_snapshot_is_a_no_op(tmp_path: Path):
    source = _db(
        tmp_path / "prev" / "warehouse.duckdb",
        SNAPSHOT + "delete from history.snap_co2_estimates;",
    )

    summary = run(source, tmp_path / "wh" / "warehouse.duckdb")

    assert summary["tables"] == []


def test_refuses_to_overwrite_existing_history(previous: Path, tmp_path: Path):
    """The snapshot is the one table a rebuild can't reproduce. Clobbering it
    has to be deliberate."""
    dest = _db(tmp_path / "wh" / "warehouse.duckdb", SNAPSHOT)

    with pytest.raises(ValueError, match="refusing to overwrite"):
        run(previous, dest)

    assert _history_rows(dest) == 2


def test_force_overwrites_existing_history(previous: Path, tmp_path: Path):
    dest = _db(
        tmp_path / "wh" / "warehouse.duckdb",
        SNAPSHOT + "delete from history.snap_co2_estimates where country_iso3 = 'FRA';",
    )

    summary = run(previous, dest, force=True)

    assert summary["rows"] == 2
    assert _history_rows(dest) == 2


def test_rejects_a_table_that_is_not_a_snapshot(tmp_path: Path):
    """Without dbt's SCD2 columns the restore would fail later, inside
    `dbt build`, with a much worse message."""
    source = _db(
        tmp_path / "prev" / "warehouse.duckdb",
        """
        create schema history;
        create table history.snap_co2_estimates as
        select * from (values ('DEU', 2019, 700.0)) t(country_iso3, year, co2_mt);
        """,
    )

    with pytest.raises(ValueError, match="not a dbt snapshot"):
        run(source, tmp_path / "wh" / "warehouse.duckdb")


def test_missing_source_is_an_error(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        run(tmp_path / "nope.duckdb", tmp_path / "wh" / "warehouse.duckdb")


def test_refuses_to_restore_a_warehouse_onto_itself(previous: Path):
    with pytest.raises(ValueError, match="same file"):
        run(previous, previous)


def test_a_landing_table_in_the_source_is_deliberately_not_carried(tmp_path: Path):
    """`raw` is no longer in this file, and the rule that carried it is gone.

    dlt lands in the DuckLake catalog now, so a published `warehouse.duckdb`
    holds nothing from `raw` for a rule to find and `CARRIED` names `history`
    alone. This test exists because the consequence is quiet and expensive:
    `weather_watermark()` reads the destination, a fresh runner's catalog is
    empty, and every release therefore cold-starts the archive at
    `WEATHER_COLD_START_YEARS` without anything going red.

    Asserting it against a source that *does* hold the table is the point — the
    old behaviour would carry it, so this fails the moment the rule comes back
    without the publishing decision that has to come with it.
    """
    source = _db(tmp_path / "prev" / "warehouse.duckdb", SNAPSHOT + WEATHER)
    dest = tmp_path / "new" / "warehouse.duckdb"

    summary = run(source, dest)

    assert summary["tables"] == [{"table": "history.snap_co2_estimates", "rows": 2}]
    assert summary["rows"] == 2


def test_the_allowlist_mechanism_still_works_for_a_landing_schema(tmp_path: Path):
    """The `Carry` rule this project stopped using is still correct, and stays
    tested at the package level.

    `modern_data_stack.history` is domain-neutral: the reason this project has no
    `raw` rule today is that its landing tables moved to another file, not that
    carrying a landing schema became wrong. Dropping the coverage with the rule
    would leave the next project to rediscover why `tables` is not optional —
    copying `raw` whole brings dlt's `_dlt_loads` with it, and the next load then
    believes the destination is not fresh and skips.
    """
    source = _db(tmp_path / "prev" / "warehouse.duckdb", SNAPSHOT + WEATHER)
    dest = tmp_path / "new" / "warehouse.duckdb"

    carry = (
        Carry(schema="history", kind="dbt snapshot", required_columns=SCD2_COLUMNS),
        Carry(
            schema="raw",
            kind="dlt landing table",
            required_columns=DLT_COLUMNS,
            tables=("om_weather_daily",),
        ),
    )
    summary = history.restore(source, dest, carry=carry)

    assert [t["table"] for t in summary["tables"]] == [
        "history.snap_co2_estimates",
        "raw.om_weather_daily",
    ]

    con = duckdb.connect(str(dest), read_only=True)
    try:
        landed = {
            name
            for (name,) in con.execute(
                "select table_name from duckdb_tables() where schema_name = 'raw'"
            ).fetchall()
        }
    finally:
        con.close()
    assert landed == {"om_weather_daily"}, f"carried more of `raw` than intended: {landed}"


def test_the_allowlist_mechanism_still_rejects_a_table_without_dlt_provenance(tmp_path: Path):
    """Measured, not assumed: carried without `_dlt_load_id`/`_dlt_id` the next
    load dies with DuckDB's `Adding columns with constraints not yet supported`,
    because dlt tries to add the column NOT NULL to a table that has rows."""
    source = _db(
        tmp_path / "prev" / "warehouse.duckdb",
        """
        create schema raw;
        create table raw.om_weather_daily as
        select * from (values ('DEU', date '2024-01-01', 3.2))
            t(country_iso3, weather_date, temperature_2m_mean);
        """,
    )
    carry = (
        Carry(
            schema="raw",
            kind="dlt landing table",
            required_columns=DLT_COLUMNS,
            tables=("om_weather_daily",),
        ),
    )

    with pytest.raises(ValueError, match="not a dlt landing table"):
        history.restore(source, tmp_path / "wh" / "warehouse.duckdb", carry=carry)


def test_a_source_without_the_weather_table_is_not_an_error(previous: Path, tmp_path: Path):
    """The first release to carry a new table has a predecessor that predates
    it. Treating the gap as fatal would break exactly one release."""
    summary = run(previous, tmp_path / "wh" / "warehouse.duckdb")

    assert summary["tables"] == [{"table": "history.snap_co2_estimates", "rows": 2}]


def test_irreplaceable_rows_counts_every_carried_relation(tmp_path: Path):
    """What `just clean warehouse` asks before deleting the file.

    Two rows now, not four: the weather archive is real state and is no longer
    *here*, so this gate no longer speaks for it. Deleting `data/lakehouse/` is
    the act that costs days of API budget, and `just clean` guards that by not
    listing the directory at all.
    """
    warehouse = _db(tmp_path / "wh" / "warehouse.duckdb", SNAPSHOT + WEATHER)

    assert irreplaceable_rows(warehouse) == 2
    assert irreplaceable_rows(tmp_path / "gone.duckdb") == 0


def test_the_carried_schema_is_the_one_dlt_loads_into():
    """`RAW_SCHEMA` is a hand-written copy of dlt's `dataset_name`. If they ever
    disagree the refusal checks one schema while the restore writes another."""
    from ingest.pipeline import PIPELINE_DATASET

    assert restore_history.RAW_SCHEMA == PIPELINE_DATASET


def test_refuses_to_restore_the_lakehouse_when_dlt_has_local_state(tmp_path, monkeypatch):
    """Measured, and re-measured after the mechanism changed completely.

    This refusal was written when a landing *table* was carried into the
    warehouse's `raw` schema. The carry is now a directory copy of a DuckLake
    catalog — different code, different artifact — and the failure is byte for
    byte the same: dlt with local state trusts what it knows, goes to update its
    stored schema against a destination that has no `_dlt_version`, and dies.
    Verified against the new path rather than inherited from the old one.
    """
    source = _db(tmp_path / "prev" / "warehouse.duckdb", SNAPSHOT)
    _published_lakehouse(tmp_path / "prev")
    state = tmp_path / "dlt" / "pipelines" / "modern_data_stack"
    monkeypatch.setattr(lakehouse, "_local_pipeline_state", lambda: state)

    dest = tmp_path / "wh" / "warehouse.duckdb"
    with pytest.raises(RuntimeError, match="_dlt_version"):
        run(source, dest, lakehouse_dir=tmp_path / "lh")

    assert not (tmp_path / "lh").exists(), "refused after writing"
    # **And the snapshot half, which is the one that used to slip through.**
    # `run` writes two artifacts. The refusal lived inside the second, so the
    # first had already happened by the time it fired: the destination's
    # `history` schema replaced by the previous release's, the landing zone
    # untouched, and a `RuntimeError` claiming nothing had been done. A refusal
    # is a promise about what did *not* happen, so it has to be asked before the
    # first write, not before the second.
    assert not dest.exists(), "history was restored before the refusal fired"


def test_the_refusal_leaves_existing_history_untouched(tmp_path, monkeypatch):
    """The same defect where it costs something rather than merely being untidy.

    A destination that already holds local snapshot history is the case
    `--force` exists to guard, and the partial restore drove straight through it:
    `history.restore` ran first, saw no reason to stop (the *source* is a
    legitimate release), and overwrote months of local revisions with last
    month's — then the lakehouse step refused, so the operator saw an error and
    had no reason to think anything had been written.
    """
    source = _db(tmp_path / "prev" / "warehouse.duckdb", SNAPSHOT)
    _published_lakehouse(tmp_path / "prev")
    dest = _db(
        tmp_path / "wh" / "warehouse.duckdb",
        SNAPSHOT.replace("'DEU-2019'", "'LOCAL-2019'"),
    )
    state = tmp_path / "dlt" / "pipelines" / "modern_data_stack"
    monkeypatch.setattr(lakehouse, "_local_pipeline_state", lambda: state)

    with pytest.raises(RuntimeError, match="_dlt_version"):
        run(source, dest, force=True, lakehouse_dir=tmp_path / "lh")

    con = duckdb.connect(str(dest), read_only=True)
    try:
        held = scalar(
            con, "select count(*) from history.snap_co2_estimates where country_year = 'LOCAL-2019'"
        )
    finally:
        con.close()
    assert held == 1, "the local history was overwritten by a restore that then refused"


def _published_lakehouse(release_dir: Path) -> None:
    """A minimal relocatable catalog, tarred the way the release ships it."""
    import tarfile
    import tempfile

    import duckdb as _duckdb

    from modern_data_stack.ducklake import attach, set_data_path

    staging = Path(tempfile.mkdtemp())
    dest = staging / "lakehouse"
    (dest / "data").mkdir(parents=True, exist_ok=True)
    con = _duckdb.connect()
    attach(con, dest / "catalog.duckdb", dest / "data", alias="lh")
    con.execute("create schema lh.raw")
    con.execute(
        "create table lh.raw.om_weather_daily as select * from (values "
        "('DEU', date '2024-01-01', 3.2, 'l', 'i')) "
        "t(country_iso3, weather_date, temperature_2m_mean, _dlt_load_id, _dlt_id)"
    )
    con.close()
    set_data_path(dest / "catalog.duckdb", "data/")

    release_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(release_dir / restore_history.LAKEHOUSE_ASSET, "w:gz") as tar:
        tar.add(dest, arcname="lakehouse")


def test_a_history_only_restore_is_unaffected_by_local_dlt_state(tmp_path, monkeypatch):
    """The flow that already worked has to keep working. Carrying `history`
    never creates the `raw` schema, so dlt's state is not contradicted and the
    refusal must not fire — otherwise this change breaks the recipe it extends."""
    source = _db(tmp_path / "prev" / "warehouse.duckdb", SNAPSHOT)
    state = tmp_path / "dlt" / "pipelines" / "modern_data_stack"
    monkeypatch.setattr(lakehouse, "_local_pipeline_state", lambda: state)

    summary = run(source, tmp_path / "wh" / "warehouse.duckdb")

    assert summary["tables"] == [{"table": "history.snap_co2_estimates", "rows": 2}]
    assert summary["lakehouse"] == {}, "no published lakehouse, so nothing to refuse over"


def test_carries_the_published_lakehouse_in_beside_the_snapshot(tmp_path):
    """Both halves of the release, one command. `history` is unreproducible in
    principle and the weather archive within a budget; a release has the pair or
    it has neither, so finding the directory beside the database is what keeps
    them from drifting apart."""
    source = _db(tmp_path / "prev" / "warehouse.duckdb", SNAPSHOT)
    _published_lakehouse(tmp_path / "prev")

    summary = run(source, tmp_path / "wh" / "warehouse.duckdb", lakehouse_dir=tmp_path / "lh")

    assert summary["tables"] == [{"table": "history.snap_co2_estimates", "rows": 2}]
    assert summary["lakehouse"] == {"raw.om_weather_daily": 1}
    assert (tmp_path / "lh" / "catalog.duckdb").exists()


def test_the_restored_lakehouse_gets_an_absolute_data_path_back(tmp_path):
    """The published catalog is relative so a consumer can open it anywhere; a
    working one cannot be, because dlt reads it from the repo root and dbt from
    `dbt/`. DuckLake checks the stored path on every attach and refuses a
    mismatch, so a restore that forgot to put the absolute form back would fail
    on the *next* command rather than this one."""
    import duckdb as _duckdb

    source = _db(tmp_path / "prev" / "warehouse.duckdb", SNAPSHOT)
    _published_lakehouse(tmp_path / "prev")

    run(source, tmp_path / "wh" / "warehouse.duckdb", lakehouse_dir=tmp_path / "lh")

    con = _duckdb.connect(str(tmp_path / "lh" / "catalog.duckdb"), read_only=True)
    try:
        stored = scalar(con, "select value from ducklake_metadata where key = 'data_path'")
    finally:
        con.close()
    assert Path(stored).is_absolute(), f"restored catalog kept a relative data_path: {stored}"


def test_the_release_layout_is_the_one_the_export_writes():
    """`restore_history` finds the lakehouse beside the database rather than
    being told where it is, so the two modules agree on one asset name by
    convention. A rename on either side makes the restore find nothing and
    cold-start the weather archive, with nothing going red."""
    from scripts.export_warehouse import LAKEHOUSE_ASSET as EXPORTED

    assert restore_history.LAKEHOUSE_ASSET == EXPORTED


def test_the_state_check_looks_for_the_pipeline_dlt_would_actually_use(tmp_path, monkeypatch):
    """The refusal is only as good as the directory it looks in. The path is
    built from `pipeline_name()`, so a fixture run checks the fixture pipeline's
    state and not the real one's — dlt keys state on the pipeline name, and the
    two are separate here precisely so a fixture run cannot hand its watermark
    to a real one."""
    import dlt.common.pipeline as dlt_common_pipeline

    monkeypatch.setattr(dlt_common_pipeline, "get_dlt_pipelines_dir", lambda: str(tmp_path))

    monkeypatch.setenv("INGEST_FIXTURES", "1")
    (tmp_path / "modern_data_stack_fixtures").mkdir()
    found = _real_state_lookup()
    assert found is not None, "did not find the fixture pipeline's state directory"
    assert found.name == "modern_data_stack_fixtures"

    monkeypatch.delenv("INGEST_FIXTURES")
    assert _real_state_lookup() is None, "found the fixture pipeline's state for a real run"
