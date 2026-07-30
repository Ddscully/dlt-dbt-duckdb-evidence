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

from scripts.restore_history import run

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
        (rows,) = con.execute("select count(*) from history.snap_co2_estimates").fetchone()
        return rows
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
        assert con.execute("select count(*) from raw.t").fetchone()[0] == 1
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
