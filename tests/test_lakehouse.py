"""Unit tests for the DuckLake lakehouse.

The archive's tests pin a directory layout; these pin a *change log*, which is
the whole reason the second layer exists. Every assertion here is about the
difference between a run that should leave a trace and one that should not —
because the failure this layer is built to avoid is not a crash, it is a catalog
that records a snapshot for a run over data nobody touched.

No network and no real warehouse: a miniature DuckDB file with dlt's two
bookkeeping columns on it is enough.
"""

from __future__ import annotations

from datetime import date

import duckdb
import pytest

from lake import lakehouse
from modern_data_stack.ducklake import Synced

RULE = Synced(
    table="raw.om_weather_daily",
    key=("country_iso3", "weather_date"),
    provenance_columns=("_dlt_load_id", "_dlt_id"),
)

# The project's real rules, captured before the autouse fixture below replaces
# them. The two tests at the bottom of this file guard that tuple against the
# rest of the tree, and reading it through the module would have them guarding
# `RULE` above instead — passing whatever the project said, which is the exact
# shape of a test that measures nothing. Verified by mutation: with
# `lakehouse.SYNCED_TABLES` those two stayed green through a merge key changed
# to `("country_iso3",)`.
DECLARED = lakehouse.SYNCED_TABLES


@pytest.fixture
def warehouse(tmp_path):
    """Three days of weather for two countries, stamped the way dlt stamps it."""
    path = tmp_path / "warehouse.duckdb"
    con = duckdb.connect(str(path))
    con.sql("create schema raw")
    con.sql(
        """
        create table raw.om_weather_daily as
        select * from (values
            ('DEU', date '2022-01-01', 1.0, 'load-1', 'id-a'),
            ('DEU', date '2022-01-02', 2.0, 'load-1', 'id-b'),
            ('FRA', date '2022-01-01', 5.0, 'load-1', 'id-c')
        ) as t(country_iso3, weather_date, temperature_2m_mean, _dlt_load_id, _dlt_id)
        """
    )
    con.close()
    return path


@pytest.fixture(autouse=True)
def _one_table(monkeypatch):
    monkeypatch.setattr(lakehouse, "SYNCED_TABLES", (RULE,))


def edit(warehouse, sql):
    con = duckdb.connect(str(warehouse))
    con.sql(sql)
    con.close()


def read(lake_dir, sql):
    """Query the lakehouse the way a consumer would — through the catalog."""
    con = duckdb.connect()
    con.execute("install ducklake")
    con.execute("load ducklake")
    con.execute(
        f"attach 'ducklake:{lakehouse.catalog_path(lake_dir)}' as lakehouse "
        f"(data_path '{lakehouse.data_path(lake_dir)}/')"
    )
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_the_first_run_creates_the_table_and_counts_every_row_as_an_insert(warehouse, tmp_path):
    summary = lakehouse.run(str(warehouse), str(tmp_path / "lh"))["raw.om_weather_daily"]

    assert (summary["rows"], summary["inserted"], summary["updated"], summary["deleted"]) == (
        3,
        3,
        0,
        0,
    )
    assert summary["snapshot"] is not None
    assert summary["files"] == 1


def test_an_unchanged_rerun_writes_nothing_at_all(warehouse, tmp_path):
    """The property the whole layer is for.

    A literal port of `lake.archive`'s delete-and-rewrite gives a no-op run and a
    real restatement *identical* catalog entries, so the format buys nothing.
    What makes them different is the `when matched and (… is distinct from …)`
    guard: with it, a run over unmoved data produces no snapshot to read.
    """
    lake_dir = str(tmp_path / "lh")
    first = lakehouse.run(str(warehouse), lake_dir)["raw.om_weather_daily"]
    again = lakehouse.run(str(warehouse), lake_dir)["raw.om_weather_daily"]

    assert again["snapshot"] is None
    assert (again["inserted"], again["updated"], again["deleted"]) == (0, 0, 0)
    assert again["files"] == first["files"]
    assert again["bytes"] == first["bytes"]


def test_a_restated_value_keeps_both_versions_in_the_change_feed(warehouse, tmp_path):
    """What the hive archive cannot say.

    `sha256sum` over the partitions reports which *file* moved; this reports
    which row, and what it used to be.
    """
    lake_dir = str(tmp_path / "lh")
    lakehouse.run(str(warehouse), lake_dir)
    edit(
        warehouse,
        "update raw.om_weather_daily set temperature_2m_mean = 9.5 "
        "where country_iso3 = 'DEU' and weather_date = date '2022-01-02'",
    )
    summary = lakehouse.run(str(warehouse), lake_dir)["raw.om_weather_daily"]

    assert (summary["updated"], summary["inserted"], summary["deleted"]) == (1, 0, 0)
    snapshot = summary["snapshot"]
    assert read(
        lake_dir,
        f"""
        select change_type, country_iso3, weather_date, temperature_2m_mean
        from ducklake_table_changes('lakehouse', 'raw', 'om_weather_daily', {snapshot}, {snapshot})
        order by change_type
        """,
    ) == [
        ("update_postimage", "DEU", date(2022, 1, 2), 9.5),
        ("update_preimage", "DEU", date(2022, 1, 2), 2.0),
    ]


def test_a_row_that_vanished_upstream_is_deleted(warehouse, tmp_path):
    """`lake.archive` deletes each directory before writing precisely so a row
    that disappeared upstream cannot keep answering queries. DuckLake's `MERGE`
    allows one UPDATE/DELETE action, so the prune is a second statement — and
    dropping it would leave this layer with the defect and no `rmtree` to cover
    it."""
    lake_dir = str(tmp_path / "lh")
    lakehouse.run(str(warehouse), lake_dir)
    edit(warehouse, "delete from raw.om_weather_daily where country_iso3 = 'FRA'")
    summary = lakehouse.run(str(warehouse), lake_dir)["raw.om_weather_daily"]

    assert (summary["deleted"], summary["rows"]) == (1, 2)
    assert read(
        lake_dir, "select count(*) from lakehouse.raw.om_weather_daily where country_iso3 = 'FRA'"
    ) == [(0,)]


def test_a_new_load_id_on_unchanged_weather_is_not_a_revision(warehouse, tmp_path):
    """The finding that decides `Synced.provenance_columns`.

    dlt regenerates `_dlt_load_id` *and* `_dlt_id` for every row it re-merges,
    byte-identical values or not — measured by loading one fixture three times
    and watching both columns change under unchanged weather. A routine ingest
    re-merges 41 x 90 = 3,690 rows, so comparing those columns would report the
    entire merge window as restated on every single run and the change feed
    would be noise.
    """
    lake_dir = str(tmp_path / "lh")
    lakehouse.run(str(warehouse), lake_dir)
    edit(
        warehouse,
        "update raw.om_weather_daily set _dlt_load_id = 'load-2', _dlt_id = _dlt_id || 'x'",
    )
    summary = lakehouse.run(str(warehouse), lake_dir)["raw.om_weather_daily"]

    assert summary["snapshot"] is None
    assert (summary["inserted"], summary["updated"], summary["deleted"]) == (0, 0, 0)
    # And the cost of that, stated: the mirrored id is the load that last
    # *changed* the row, not the one that last touched it.
    assert read(lake_dir, "select distinct _dlt_load_id from lakehouse.raw.om_weather_daily") == [
        ("load-1",)
    ]


def test_a_real_change_arriving_with_a_new_load_id_is_still_caught(warehouse, tmp_path):
    """The other side of the previous test, and the reason it needs one.

    Ignoring the provenance columns must not ignore the *row* they sit on — a
    guard that skipped any row whose load id moved would pass the test above and
    silently stop archiving anything at all.
    """
    lake_dir = str(tmp_path / "lh")
    lakehouse.run(str(warehouse), lake_dir)
    edit(
        warehouse,
        "update raw.om_weather_daily set temperature_2m_mean = 3.5, _dlt_load_id = 'load-2' "
        "where country_iso3 = 'FRA'",
    )
    summary = lakehouse.run(str(warehouse), lake_dir)["raw.om_weather_daily"]

    assert (summary["updated"], summary["inserted"], summary["deleted"]) == (1, 0, 0)
    assert read(
        lake_dir,
        "select temperature_2m_mean, _dlt_load_id from lakehouse.raw.om_weather_daily where country_iso3 = 'FRA'",
    ) == [(3.5, "load-2")]


def test_the_configured_inlining_limit_keeps_a_small_change_in_parquet(warehouse, tmp_path):
    """`DATA_INLINING_ROW_LIMIT = 0` is why a one-row test means anything.

    On DuckLake's default of 10 a change this size is written into the catalog
    database instead of out to files — so a fixture restating one row would
    exercise a path the real 3,690-row merge window never takes.
    """
    lake_dir = str(tmp_path / "lh")
    first = lakehouse.run(str(warehouse), lake_dir)["raw.om_weather_daily"]
    edit(
        warehouse,
        "update raw.om_weather_daily set temperature_2m_mean = 9.5 where country_iso3 = 'FRA'",
    )
    after = lakehouse.run(str(warehouse), lake_dir)["raw.om_weather_daily"]

    assert lakehouse.DATA_INLINING_ROW_LIMIT == 0
    assert after["files"] > first["files"]


def test_a_missing_key_column_is_refused_by_name(warehouse, tmp_path, monkeypatch):
    """A merge key that is not in the table would otherwise fail inside the
    generated SQL, naming the ON clause rather than the rule that wrote it."""
    monkeypatch.setattr(
        lakehouse, "SYNCED_TABLES", (Synced(table="raw.om_weather_daily", key=("station_id",)),)
    )
    with pytest.raises(ValueError, match="station_id"):
        lakehouse.run(str(warehouse), str(tmp_path / "lh"))


def test_a_rule_needs_a_key_and_a_qualified_name():
    """Both would otherwise produce a syntactically valid merge with no `on`
    condition, or a lakehouse table in the wrong schema."""
    with pytest.raises(ValueError, match="merge key"):
        Synced(table="raw.om_weather_daily", key=())
    with pytest.raises(ValueError, match="qualified"):
        Synced(table="om_weather_daily", key=("country_iso3",))


def test_the_weather_merge_key_is_the_one_dlt_merges_on():
    """A hand-written copy of `WEATHER_PRIMARY_KEY`, held to it.

    They have to agree: dlt decides which rows are the same row when it lands
    them, and this decides the same thing when they are mirrored. Diverge and
    the lakehouse either collapses distinct rows or records every restatement as
    an insert beside the old value — neither of which is an error.
    """
    from ingest.pipeline import WEATHER_PRIMARY_KEY

    rule = next(r for r in DECLARED if r.table == "raw.om_weather_daily")
    assert rule.key == WEATHER_PRIMARY_KEY


def test_every_landing_table_names_dlts_columns_as_provenance():
    """Adding a second `raw.` rule without this is a silent regression: the new
    table would report its whole merge window as revised on every ingest."""
    from modern_data_stack.history import DLT_COLUMNS

    for rule in DECLARED:
        if rule.schema == "raw":
            assert rule.provenance_columns == DLT_COLUMNS, rule.table
        else:
            assert rule.provenance_columns == (), rule.table
