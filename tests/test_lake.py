"""Unit tests for the Parquet archive.

No network and no real warehouse: a two-table DuckDB file in a temp directory is
enough to pin the things that would actually go wrong — the hive layout, the
read-back parity the Dagster check relies on, and the stale-partition case that
`overwrite true` exists for.
"""

from __future__ import annotations

import duckdb
import pytest

from lake import archive


@pytest.fixture
def warehouse(tmp_path):
    """A miniature warehouse with the two schemas the archive reads."""
    path = tmp_path / "warehouse.duckdb"
    con = duckdb.connect(str(path))
    con.sql("create schema raw")
    con.sql(
        """
        create table raw.owid_co2 as
        select * from (values ('USA', 2020, 5000.0), ('USA', 2021, 5100.0),
                              ('KEN', 2020, 18.0)) as t(iso_code, year, co2)
        """
    )
    con.sql("create schema marts")
    con.sql(
        """
        create table marts.fct_emissions_energy as
        select * from (values ('USA', 2020, 5000.0), ('KEN', 2021, 19.0)) as t(country_iso3, year, co2_mt)
        """
    )
    con.close()
    return path


@pytest.fixture(autouse=True)
def _two_tables(monkeypatch):
    monkeypatch.setattr(archive, "ARCHIVED_TABLES", ("raw.owid_co2", "marts.fct_emissions_energy"))


def test_writes_one_hive_partition_per_year(warehouse, tmp_path):
    lake = tmp_path / "lake"
    summary = archive.run(str(warehouse), str(lake))

    assert sorted(p.name for p in (lake / "raw_owid_co2").iterdir()) == ["year=2020", "year=2021"]
    assert summary["raw.owid_co2"] == {
        "rows": 3,
        "partitions": 2,
        "files": 2,
        "bytes": summary["raw.owid_co2"]["bytes"],  # size isn't the assertion
    }
    assert summary["raw.owid_co2"]["bytes"] > 0


def test_read_back_matches_the_warehouse(warehouse, tmp_path):
    """The parity the `lake_matches_warehouse` asset check asserts: the year
    column survives as a hive partition, so nothing is lost by moving it into the
    path."""
    lake = tmp_path / "lake"
    archive.run(str(warehouse), str(lake))

    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        for table in ("raw.owid_co2", "marts.fct_emissions_energy"):
            glob = f"{archive.table_dir(lake, table)}/**/*.parquet"
            assert con.sql(f"select count(*), min(year), max(year) from {table}").fetchone() == (
                con.sql(
                    f"select count(*), min(year), max(year) "
                    f"from read_parquet('{glob}', hive_partitioning = 1)"
                ).fetchone()
            )
    finally:
        con.close()


def test_rerun_drops_a_partition_that_lost_its_rows(warehouse, tmp_path):
    """`overwrite true`, not `overwrite_or_ignore`: a year deleted upstream has to
    disappear from the archive, or the lake keeps answering with data the
    warehouse no longer has."""
    lake = tmp_path / "lake"
    archive.run(str(warehouse), str(lake))
    assert (lake / "raw_owid_co2" / "year=2021").exists()

    con = duckdb.connect(str(warehouse))
    con.sql("delete from raw.owid_co2 where year = 2021")
    con.close()

    summary = archive.run(str(warehouse), str(lake))
    assert not (lake / "raw_owid_co2" / "year=2021").exists()
    assert summary["raw.owid_co2"]["rows"] == 2
