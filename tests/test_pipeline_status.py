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

from modern_data_stack import observability
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


def _range_node():
    return {
        "resource_type": "test",
        "name": "dbt_utils_accepted_range_fct_emissions_energy_co2_mt__0",
        "alias": "dbt_utils_accepted_range_fct_e_abc123",
        "attached_node": "model.demo.fct_emissions_energy",
        "test_metadata": {"name": "accepted_range", "kwargs": {"column_name": "co2_mt"}},
    }


def _not_null_node():
    return {
        "resource_type": "test",
        "name": "not_null_fct_emissions_energy_co2_mt",
        "alias": "not_null_fct_emissions_energy_co2_mt",
        "attached_node": "model.demo.fct_emissions_energy",
        "test_metadata": {"name": "not_null", "kwargs": {"column_name": "co2_mt"}},
    }


@pytest.fixture
def manifest(tmp_path):
    """A manifest naming both audit tables — i.e. neither of them is stale."""
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps({"nodes": {"test.demo.range": _range_node(), "test.demo.nn": _not_null_node()}})
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


def test_an_empty_exclude_prefix_excludes_nothing(warehouse):
    """`not like '' || '%'` is `not like '%'`, which matches no row at all — so an
    empty prefix has to drop the predicate rather than pass it. Passing it gives
    an empty inventory, which surfaces two calls later as polars' "must have at
    least one column" out of `write_status`, naming neither the parameter nor the
    cause. A reuser with no `pipeline_*` tables is the one who'd hit it.
    """
    con = duckdb.connect(str(warehouse))
    con.sql("create table marts.pipeline_tables as select 1 as n")
    try:
        default = observability.build_tables(con, ("marts",))
        everything = observability.build_tables(con, ("marts",), exclude_prefix="")
    finally:
        con.close()

    assert default["table_name"].to_list() == ["marts.fct_emissions_energy"]
    assert everything["table_name"].to_list() == [
        "marts.fct_emissions_energy",
        "marts.pipeline_tables",
    ]


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


def test_an_audit_table_the_manifest_does_not_name_is_dropped_as_stale(warehouse, tmp_path):
    """dbt writes the audit schema every build but never removes a dead table.

    Renaming a model orphans every audit table attached to it, because the alias
    hash is over the test's arguments — versioning `fct_emissions_energy` to
    `_v2` left 17 `dbt_utils_accepted_range_fct_e_<hash>` tables behind. They are
    empty, so they scored as passing and silently inflated the test count while
    showing no model.
    """
    path = tmp_path / "partial.json"
    path.write_text(json.dumps({"nodes": {"test.demo.range": _range_node()}}))

    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        frame = observability.build_tests(con, str(path))
    finally:
        con.close()

    # The `not_null` audit table exists in the warehouse but not in the manifest.
    assert frame.height == 1
    assert frame.to_dicts()[0]["tested_model"] == "fct_emissions_energy"


def _equal_rowcount_case(tmp_path, diff_count):
    """A warehouse + manifest for one `equal_rowcount` test with the given diff.

    `dbt_utils.equal_rowcount` returns a one-row *summary* whether it passed or
    failed, so the audit table is never empty and row-counting cannot read it.
    """
    path = tmp_path / "eq.duckdb"
    con = duckdb.connect(str(path))
    con.sql("create schema dbt_test__audit")
    con.sql(
        f"""
        create table dbt_test__audit.dbt_utils_equal_rowcount_fct_f_deadbeef as
        select * from (values (1, 1, 265035, 265035, {diff_count}))
        as t(id_dbtutils_test_equal_rowcount_a, id_dbtutils_test_equal_rowcount_b,
             count_a, count_b, diff_count)
        """
    )
    con.close()

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "nodes": {
                    "test.demo.eq": {
                        "resource_type": "test",
                        "name": "dbt_utils_equal_rowcount_fct_fx_rates_published_ref_stg_fx_rates_",
                        "alias": "dbt_utils_equal_rowcount_fct_f_deadbeef",
                        "attached_node": "model.demo.fct_fx_rates_published",
                        "test_metadata": {"name": "equal_rowcount", "kwargs": {}},
                        "config": {
                            "fail_calc": "sum(coalesce(diff_count, 0))",
                            "severity": "ERROR",
                        },
                    }
                }
            }
        )
    )
    return path, manifest


def test_a_passing_equal_rowcount_is_not_reported_as_a_failure(tmp_path):
    """The bug this fixes: `count(*)` scored a passing test as one failing row.

    Both `equal_rowcount` tests in this project reported `status='fail'` against a
    `dbt build` that finished PASS=387, ERROR=0 — so the page whose job is
    reporting pipeline health contradicted the build. The verdict is the test's
    `fail_calc` applied to its result set, not the size of that result set.
    """
    warehouse, manifest = _equal_rowcount_case(tmp_path, diff_count=0)
    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        row = observability.build_tests(con, str(manifest)).to_dicts()[0]
    finally:
        con.close()

    assert row["failing_rows"] == 0
    assert row["status"] == "pass"
    assert row["test_type"] == "equal_rowcount"


def test_a_genuinely_failing_equal_rowcount_reports_the_row_difference(tmp_path):
    """The other half: `fail_calc` must still count a real failure, and count it
    as the *difference* rather than as the one summary row."""
    warehouse, manifest = _equal_rowcount_case(tmp_path, diff_count=42)
    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        row = observability.build_tests(con, str(manifest)).to_dicts()[0]
    finally:
        con.close()

    assert row["failing_rows"] == 42
    assert row["status"] == "fail"


def test_a_warn_severity_test_with_failures_is_not_called_a_failure(warehouse, tmp_path):
    """dbt does not fail a build on a warn-severity test, so neither does this.

    Nothing in the project uses `severity: warn` today; without this the first one
    added would show up as a red pipeline for something dbt deliberately let
    through.
    """
    manifest = tmp_path / "warn.json"
    manifest.write_text(
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
                        "config": {"severity": "warn"},
                    }
                }
            }
        )
    )
    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        rows = {
            row["audit_table"]: row
            for row in observability.build_tests(con, str(manifest)).to_dicts()
        }
    finally:
        con.close()

    warned = rows["dbt_test__audit.dbt_utils_accepted_range_fct_e_abc123"]
    assert warned["failing_rows"] == 1
    assert warned["severity"] == "warn"
    assert warned["status"] == "warn"


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
