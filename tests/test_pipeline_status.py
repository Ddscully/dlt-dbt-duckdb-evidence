"""Unit tests for the pipeline-observability tables.

No network and no real warehouse: a miniature DuckDB file with one landing
table, one modelled table and two `dbt_test__audit` tables is enough to pin the
things that would actually go wrong — the `_dlt_load_id` epoch conversion, the
pass/fail split, and the manifest lookup that turns a truncated audit-table name
back into a readable test.
"""

from __future__ import annotations

import json

import duckdb
import pytest

from transform import pipeline_status


@pytest.fixture
def warehouse(tmp_path):
    """A miniature warehouse covering each of the three inventories."""
    path = tmp_path / "warehouse.duckdb"
    con = duckdb.connect(str(path))

    con.sql("create schema raw")
    # `_dlt_load_id` is a varchar holding a unix epoch — 2021-01-01T00:00:00Z.
    con.sql(
        """
        create table raw.owid_co2 as
        select * from (values ('USA', 2020, '1609459200.0'),
                              ('KEN', 2021, '1609459200.0'))
        as t(iso_code, year, _dlt_load_id)
        """
    )

    con.sql("create schema marts")
    con.sql(
        """
        create table marts.fct_emissions_energy as
        select * from (values ('USA', 2020, 5000.0), ('KEN', 2021, 19.0))
        as t(country_iso3, year, co2_mt)
        """
    )

    con.sql("create schema dbt_test__audit")
    # A passing test leaves an empty table behind; a failing one leaves rows.
    con.sql(
        "create table dbt_test__audit.not_null_fct_emissions_energy_co2_mt (country_iso3 varchar)"
    )
    con.sql(
        """
        create table dbt_test__audit.dbt_utils_accepted_range_fct_e_abc123 as
        select * from (values ('KEN', 2021)) as t(country_iso3, year)
        """
    )
    con.close()
    return path


@pytest.fixture(autouse=True)
def _one_source_table(monkeypatch):
    monkeypatch.setattr(pipeline_status, "SOURCE_TABLES", ("owid_co2",))
    monkeypatch.setattr(pipeline_status, "LAYERS", ("marts",))


@pytest.fixture
def manifest(tmp_path):
    """A manifest naming one of the two audit tables, to exercise both paths."""
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "nodes": {
                    "test.demo.range": {
                        "resource_type": "test",
                        "name": "dbt_utils_accepted_range_fct_emissions_energy_co2_mt__0",
                        "alias": "dbt_utils_accepted_range_fct_e_abc123",
                        "attached_node": "model.demo.fct_emissions_energy",
                        "test_metadata": {
                            "name": "accepted_range",
                            "kwargs": {"column_name": "co2_mt"},
                        },
                    }
                }
            }
        )
    )
    return path


def test_sources_resolve_the_dlt_epoch(warehouse):
    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        frame = pipeline_status.build_sources(con)
    finally:
        con.close()

    row = frame.to_dicts()[0]
    assert row["source_table"] == "raw.owid_co2"
    assert row["rows"] == 2
    assert (row["year_min"], row["year_max"]) == (2020, 2021)
    # The varchar epoch became a real timestamp, not a string or a 1970 date.
    assert row["loaded_at"].year == 2021


def test_tables_report_rows_and_year_span(warehouse):
    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        frame = pipeline_status.build_tables(con)
    finally:
        con.close()

    row = frame.to_dicts()[0]
    assert row["table_name"] == "marts.fct_emissions_energy"
    assert row["rows"] == 2
    assert (row["year_min"], row["year_max"]) == (2020, 2021)


def test_tests_split_pass_from_fail(warehouse, manifest):
    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        frame = pipeline_status.build_tests(con, str(manifest))
    finally:
        con.close()

    by_status = {row["status"]: row for row in frame.to_dicts()}
    assert by_status["pass"]["failing_rows"] == 0
    assert by_status["fail"]["failing_rows"] == 1

    # The manifest turns the truncated alias back into the real test name and
    # the model it guards; without it the table name is the only label there is.
    failing = by_status["fail"]
    assert failing["test_name"].startswith("dbt_utils_accepted_range_fct_emissions_energy")
    assert failing["tested_model"] == "fct_emissions_energy"
    assert failing["tested_column"] == "co2_mt"
    assert failing["audit_table"].startswith("dbt_test__audit.")


def test_tests_survive_a_missing_manifest(warehouse, tmp_path):
    """`dbt/target/` is gitignored, so the manifest can legitimately be absent."""
    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        frame = pipeline_status.build_tests(con, str(tmp_path / "nope.json"))
    finally:
        con.close()

    assert frame.height == 2
    # Falls back to the audit table name rather than raising or dropping rows.
    assert all(row["tested_model"] is None for row in frame.to_dicts())
    assert {row["status"] for row in frame.to_dicts()} == {"pass", "fail"}


def test_run_writes_all_three_tables(warehouse, manifest):
    written = pipeline_status.run(str(warehouse), str(manifest))
    assert written == {"pipeline_sources": 1, "pipeline_tables": 1, "pipeline_tests": 2}

    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        for name in written:
            assert con.sql(f"select count(*) from analytics.{name}").fetchone()[0] > 0
    finally:
        con.close()
