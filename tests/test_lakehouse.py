"""The lakehouse: dlt's landing zone, and the revision log derived from it.

These tests replace a suite written against a *mirror* — when `raw` lived in the
DuckDB file and a gated `MERGE` copied two weather tables into DuckLake beside
it. dlt writes the catalog directly now, so the merge, the prune and their
parity check are gone, and what is left to guard is narrower and sharper: the
substitute for the change feed.

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


def test_forgetting_the_provenance_columns_reports_the_whole_table(tmp_path):
    """The mutation that proves the ignore list is load-bearing.

    This is the bug the design exists to avoid, run deliberately: compare
    without ignoring anything and an identical reload reports both rows. It
    raises nothing and returns nothing malformed — a wrong answer of the right
    shape, which is the only kind this repo has ever had trouble seeing.
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


def test_the_attach_alias_is_the_database_dbt_declares():
    """dbt's `_sources.yml` says `database: lakehouse` and `profiles.yml` attaches
    under that alias. Both are the constant here; a change to one of the three
    that misses the others means dbt cannot resolve a single source."""
    from pathlib import Path

    import yaml

    sources = yaml.safe_load(Path("dbt/models/staging/_sources.yml").read_text())
    raw = next(s for s in sources["sources"] if s["name"] == "raw")
    assert raw["database"] == lakehouse.ATTACH_ALIAS

    profile = yaml.safe_load(Path("dbt/profiles.yml").read_text())
    attached = profile["modern_data_stack"]["outputs"]["dev"]["attach"]
    assert [a["alias"] for a in attached] == [lakehouse.ATTACH_ALIAS]
