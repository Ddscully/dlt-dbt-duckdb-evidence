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

from modern_data_stack.db import scalar
from scripts import restore_history
from scripts.restore_history import CARRIED_RAW_TABLES, irreplaceable_rows, run

# Captured before the autouse fixture below replaces it, so the one test that
# exercises the real lookup can still reach it.
_real_state_lookup = restore_history._local_pipeline_state


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
    monkeypatch.setattr(restore_history, "_local_pipeline_state", lambda: None)


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


def test_carries_the_weather_archive_alongside_the_snapshot(tmp_path: Path):
    """`raw.om_weather_daily` is unreproducible within a *budget* rather than in
    principle, which has the same consequence — so one mechanism carries both."""
    source = _db(tmp_path / "prev" / "warehouse.duckdb", SNAPSHOT + WEATHER)
    dest = tmp_path / "new" / "warehouse.duckdb"

    summary = run(source, dest)

    assert summary["tables"] == [
        {"table": "history.snap_co2_estimates", "rows": 2},
        {"table": "raw.om_weather_daily", "rows": 2},
    ]
    assert summary["rows"] == 4


def test_carries_only_the_named_raw_tables_and_not_dlt_bookkeeping(tmp_path: Path):
    """The failure this prevents is silent and total. dlt keys "is this
    destination fresh?" on `_dlt_loads`; carry that and the next load skips,
    so the release ships last month's raw data believing it refetched it."""
    source = _db(tmp_path / "prev" / "warehouse.duckdb", SNAPSHOT + WEATHER)
    dest = tmp_path / "new" / "warehouse.duckdb"

    run(source, dest)

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


def test_a_source_without_the_weather_table_is_not_an_error(previous: Path, tmp_path: Path):
    """The first release to carry a new table has a predecessor that predates
    it. Treating the gap as fatal would break exactly one release."""
    summary = run(previous, tmp_path / "wh" / "warehouse.duckdb")

    assert summary["tables"] == [{"table": "history.snap_co2_estimates", "rows": 2}]


def test_rejects_a_landing_table_without_dlt_provenance(tmp_path: Path):
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

    with pytest.raises(ValueError, match="not a dlt landing table"):
        run(source, tmp_path / "wh" / "warehouse.duckdb")


def test_refuses_to_overwrite_a_carried_landing_table(tmp_path: Path):
    """Same rule as the snapshot: a day of API budget is no easier to get back
    than a revision is."""
    source = _db(tmp_path / "prev" / "warehouse.duckdb", WEATHER)
    dest = _db(tmp_path / "wh" / "warehouse.duckdb", WEATHER)

    with pytest.raises(ValueError, match="refusing to overwrite"):
        run(source, dest)


def test_irreplaceable_rows_counts_every_carried_relation(tmp_path: Path):
    """What `just clean warehouse` asks before deleting the file. Counting the
    snapshot alone would wave through a weather archive costing days to refetch."""
    warehouse = _db(tmp_path / "wh" / "warehouse.duckdb", SNAPSHOT + WEATHER)

    assert irreplaceable_rows(warehouse) == 4
    assert irreplaceable_rows(tmp_path / "gone.duckdb") == 0


def test_carried_raw_tables_are_real_dlt_resources():
    """A hand-written copy of a resource name. Rename the resource without this
    and the table stops being carried in silence — the cost lands a month later,
    as a day of API budget, on a workflow nobody is watching."""
    from ingest.pipeline import FULL_REFRESH_RESOURCES, INCREMENTAL_RESOURCES

    resources = set(FULL_REFRESH_RESOURCES) | set(INCREMENTAL_RESOURCES)
    unknown = set(CARRIED_RAW_TABLES) - resources
    assert not unknown, f"carried raw tables that no dlt resource writes: {unknown}"


def test_the_carried_schema_is_the_one_dlt_loads_into():
    """`RAW_SCHEMA` is a hand-written copy of dlt's `dataset_name`. If they ever
    disagree the refusal checks one schema while the restore writes another."""
    from ingest.pipeline import PIPELINE_DATASET

    assert restore_history.RAW_SCHEMA == PIPELINE_DATASET


def test_refuses_to_carry_a_landing_table_when_dlt_has_local_state(tmp_path, monkeypatch):
    """Measured: with local state dlt trusts what it already knows, goes to
    update its stored schema against a restored file that has no `_dlt_version`,
    and dies. Refusing here names the remedy instead of the symptom."""
    source = _db(tmp_path / "prev" / "warehouse.duckdb", SNAPSHOT + WEATHER)
    state = tmp_path / "dlt" / "pipelines" / "modern_data_stack"
    monkeypatch.setattr(restore_history, "_local_pipeline_state", lambda: state)

    with pytest.raises(RuntimeError, match="_dlt_version"):
        run(source, tmp_path / "wh" / "warehouse.duckdb")

    assert not (tmp_path / "wh" / "warehouse.duckdb").exists(), "refused after writing"


def test_a_history_only_restore_is_unaffected_by_local_dlt_state(tmp_path, monkeypatch):
    """The flow that already worked has to keep working. Carrying `history`
    never creates the `raw` schema, so dlt's state is not contradicted and the
    refusal must not fire — otherwise this change breaks the recipe it extends."""
    source = _db(tmp_path / "prev" / "warehouse.duckdb", SNAPSHOT)
    state = tmp_path / "dlt" / "pipelines" / "modern_data_stack"
    monkeypatch.setattr(restore_history, "_local_pipeline_state", lambda: state)

    summary = run(source, tmp_path / "wh" / "warehouse.duckdb")

    assert summary["tables"] == [{"table": "history.snap_co2_estimates", "rows": 2}]


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
