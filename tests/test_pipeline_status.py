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
import polars as pl
import pytest

from modern_data_stack import db, observability
from modern_data_stack.db import scalar
from modern_data_stack.ducklake import attach
from transform import pipeline_status


@pytest.fixture
def warehouse(tmp_path):
    """A miniature warehouse covering each of the three inventories."""
    path = tmp_path / "warehouse.duckdb"

    # `raw` lives in the lakehouse, not in the warehouse file — so the fixture
    # builds one. Writing it into the warehouse instead would still *pass* the
    # source inventory if the catalog filter were dropped, which is exactly the
    # regression `raw_database` exists to prevent: `information_schema` spans
    # every attached catalog, so a `raw` schema in either would match.
    lake_dir = tmp_path / "lakehouse"
    (lake_dir / "data").mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    attach(con, lake_dir / "catalog.duckdb", lake_dir / "data", alias="lakehouse")
    con.sql("create schema lakehouse.raw")
    # `_dlt_load_id` is a varchar holding a unix epoch — 2021-01-01T00:00:00Z.
    con.sql(
        """
        create table lakehouse.raw.owid_co2 as
        select * from (values ('USA', 2020, '1609459200.0'),
                              ('KEN', 2021, '1609459200.0'))
        as t(iso_code, year, _dlt_load_id)
        """
    )
    con.sql("detach lakehouse")

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
    """A manifest naming both audit tables — i.e. neither of them is stale.

    It also carries the versioned model, which no *test* here needs: it is what
    lets a run-history assertion go through `pipeline_status.build_runs` and so
    check the wiring rather than the logic. Without it, dropping the `nodes`
    argument at the call site is a mutation every test survives.
    """
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "nodes": {
                    "test.demo.range": _range_node(),
                    "test.demo.nn": _not_null_node(),
                    "model.demo.fct_emissions_energy.v1": {
                        "resource_type": "model",
                        "name": "fct_emissions_energy",
                        "alias": "fct_emissions_energy_v1",
                    },
                }
            }
        )
    )
    return path


def test_sources_resolve_the_dlt_epoch(warehouse):
    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        attach(
            con,
            warehouse.parent / "lakehouse" / "catalog.duckdb",
            warehouse.parent / "lakehouse" / "data",
            alias="lakehouse",
            read_only=True,
        )
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
    least one column" out of `db.write_frames`, naming neither the parameter nor the
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


def test_a_test_on_a_versioned_model_is_labelled_with_the_relation_not_the_version():
    """`attached_node` for a versioned model ends in `.v1`, not in the model name.

    Splitting on the final dot labels every test on `fct_emissions_energy`
    **`v1`** or **`v2`** — which is what the pipeline page rendered until the
    bar chart became a table and made it readable. The alias is the relation the
    test ran against, so it agrees with `pipeline_tables` one section above.
    """
    nodes = {
        "model.demo.fct_emissions_energy.v1": {
            "name": "fct_emissions_energy",
            "alias": "fct_emissions_energy_v1",
        },
        "model.demo.fct_emissions_energy.v2": {
            "name": "fct_emissions_energy",
            "alias": "fct_emissions_energy",
        },
    }

    assert (
        observability.node_display_name("model.demo.fct_emissions_energy.v1", nodes)
        == "fct_emissions_energy_v1"
    )
    assert (
        observability.node_display_name("model.demo.fct_emissions_energy.v2", nodes)
        == "fct_emissions_energy"
    )
    # A test can attach to a source, which is not in `nodes` — hence the fallback.
    assert observability.node_display_name("source.demo.raw.owid_co2", {}) == "owid_co2"
    assert observability.node_display_name("", {}) is None


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


def test_run_writes_all_four_tables(warehouse, manifest, run_results):
    written = pipeline_status.run(
        str(warehouse),
        str(manifest),
        str(warehouse.parent / "lakehouse"),
        str(run_results),
    )
    assert written == {
        "pipeline_sources": 1,
        "pipeline_tables": 1,
        "pipeline_tests": 2,
        "pipeline_runs": 2,
    }

    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        for name in written:
            assert scalar(con, f"select count(*) from analytics.{name}") > 0
    finally:
        con.close()


@pytest.fixture
def run_results(tmp_path):
    """A `run_results.json` holding one build of two nodes.

    One of them is the versioned model, because that id is the whole reason
    `build_runs` takes the manifest at all — see the test below.
    """
    path = tmp_path / "run_results.json"
    path.write_text(
        json.dumps(
            {
                "metadata": {
                    "invocation_id": "inv-1",
                    "invocation_started_at": "2026-09-09T09:00:00.000000Z",
                    "generated_at": "2026-09-09T09:00:30.000000Z",
                    "dbt_version": "1.11.14",
                },
                "args": {"which": "build"},
                "results": [
                    {
                        "unique_id": "model.demo.fct_emissions_energy.v1",
                        "status": "success",
                        "execution_time": 1.5,
                        "timing": [
                            {
                                "name": "compile",
                                "started_at": "2026-09-09T09:00:00.000000Z",
                                "completed_at": "2026-09-09T09:00:00.250000Z",
                            },
                            {
                                "name": "execute",
                                "started_at": "2026-09-09T09:00:00.250000Z",
                                "completed_at": "2026-09-09T09:00:01.250000Z",
                            },
                        ],
                    },
                    {
                        "unique_id": "test.demo.not_null_stg_co2_year.abc123",
                        "status": "pass",
                        "execution_time": 0.25,
                        "timing": [],
                    },
                ],
            }
        )
    )
    return path


def test_a_run_row_is_labelled_with_the_relation_not_the_version(run_results):
    """The same trap as the test labels, one artifact over.

    Both versioned nodes appear in `run_results.json`, so a `build_runs` that
    split the unique id by hand would file the model's own timing under **`v1`**
    — next to a `pipeline_tables` row calling the same relation
    `fct_emissions_energy_v1`. Sharing `node_display_name` is what keeps the two
    sections of the page agreeing, and is why that helper is no longer named
    after tests.
    """
    nodes = {
        "model.demo.fct_emissions_energy.v1": {"alias": "fct_emissions_energy_v1"},
    }
    named = observability.build_runs(str(run_results), nodes)
    assert named.filter(named["resource_type"] == "model")["node_name"].to_list() == [
        "fct_emissions_energy_v1"
    ]

    # Without the manifest it degrades to the id's last segment, which is the
    # version. Asserted rather than left implicit: it is the difference the
    # `nodes` argument buys, and a future refactor that stopped passing it would
    # otherwise go green.
    bare = observability.build_runs(str(run_results))
    assert bare.filter(bare["resource_type"] == "model")["node_name"].to_list() == ["v1"]


def test_a_run_row_carries_the_command_that_produced_it(run_results):
    """`dbt test` overwrites the artifact a `dbt build` left behind.

    Without `dbt_command` a 36-row `dbt test --select test_type:unit` invocation
    and a build that happened to run 36 nodes are the same row set, and the
    trend line silently compares them.
    """
    frame = observability.build_runs(str(run_results))
    assert frame["dbt_command"].unique().to_list() == ["build"]
    assert frame["invocation_id"].unique().to_list() == ["inv-1"]


def test_the_two_timing_phases_are_split_and_do_not_have_to_sum(run_results):
    """Compile and execute are reported separately; the total is not their sum.

    Measured on the real artifact: 65.14s of `execution_time` against 57.86s of
    compile + execute, with neither phase null on any node. dbt's per-node total
    includes work outside the two named phases, so a chart that derives one from
    the other two is wrong — both are stored.
    """
    frame = observability.build_runs(str(run_results))
    model = frame.filter(frame["resource_type"] == "model")
    assert model["compile_time_s"].to_list() == [0.25]
    assert model["execute_time_s"].to_list() == [1.0]
    assert model["execution_time_s"].to_list() == [1.5]  # not 1.25

    # A node with no `timing` at all reports the total and no phases, rather
    # than zeros — a zero would read as "compiled instantly".
    test_row = frame.filter(frame["resource_type"] == "test")
    assert test_row["compile_time_s"].to_list() == [None]
    assert test_row["execute_time_s"].to_list() == [None]


def test_runs_survive_a_missing_run_results(tmp_path):
    """A warehouse that has never had a dbt build run against it is a real state.

    The empty frame has to carry the *columns* as well: an empty
    `pl.DataFrame([])` has none, and appending one would create a table with no
    columns that every later append then fails against.
    """
    frame = observability.build_runs(str(tmp_path / "absent.json"))
    assert frame.height == 0
    assert set(frame.columns) == set(observability.RUN_COLUMNS)


def test_appending_the_same_invocation_twice_adds_nothing(warehouse, run_results):
    """`just pipeline-status` is its own recipe and can be run twice on one build.

    Replaying the same artifact must not double the run's cost in the history.
    Mutation-checked: keying the insert on nothing appends 2 rows here.
    """
    frame = observability.build_runs(str(run_results))
    con = duckdb.connect(str(warehouse))
    try:
        first = db.append_frame(con, frame, "analytics", "pipeline_runs", "invocation_id")
        second = db.append_frame(con, frame, "analytics", "pipeline_runs", "invocation_id")
        assert (first, second) == (2, 0)

        # A different invocation of the same nodes does append — the guard is on
        # the run, not on the node, so a rebuild's timings are a new row set.
        again = frame.with_columns(pl.lit("inv-2").alias("invocation_id"))
        assert db.append_frame(con, again, "analytics", "pipeline_runs", "invocation_id") == 2
        assert scalar(con, "select count(distinct invocation_id) from analytics.pipeline_runs") == 2
    finally:
        con.close()


def test_the_run_history_is_kept_out_of_the_table_inventory(warehouse, manifest, run_results):
    """`pipeline_tables` must not count this module's own output.

    It is excluded by the `pipeline_` prefix rather than by name, so the fourth
    table inherited the exclusion — asserted because that is luck until it is
    checked, and a table named `run_history` would have broken it.
    """
    pipeline_status.run(
        str(warehouse), str(manifest), str(warehouse.parent / "lakehouse"), str(run_results)
    )
    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        listed = {
            name
            for (name,) in con.sql("select table_name from analytics.pipeline_tables").fetchall()
        }
    finally:
        con.close()
    # Named against the constant, not against the `pipeline_` prefix. The first
    # version of this assertion checked the prefix, so renaming the table to
    # `run_history` — which is exactly the change that breaks the exclusion —
    # left it green.
    assert f"analytics.{pipeline_status.RUNS_TABLE}" not in listed
    assert not [name for name in listed if name.startswith("analytics.pipeline_")]


def test_the_wired_builder_reads_the_manifest_for_its_node_names(manifest, run_results):
    """Through `pipeline_status`, not through `observability`, and that is the point.

    The direct test above hands `build_runs` a `nodes` dict and proves the
    lookup. It cannot see the call site dropping that argument — mutation-proven:
    deleting `observability.manifest_nodes(manifest_path)` from
    `pipeline_status.build_runs` left all seventeen tests green. This one goes
    through the wiring, so the version reaches the row only if the manifest is
    actually read.
    """
    frame = pipeline_status.build_runs(str(manifest), str(run_results))
    models = frame.filter(frame["resource_type"] == "model")
    assert models["node_name"].to_list() == ["fct_emissions_energy_v1"]
