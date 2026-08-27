"""The seven `@dg.asset_check` bodies, run without materializing anything.

`tests/test_definitions.py` proves each check is *registered* — that it will run
at all. Nothing proved that any of them would *notice*: the seven function
bodies were only ever executed by a full materialize, so `just test` could not
tell a working check from one whose logic had inverted, and the answer arrived
minutes later in `just test-pipeline` or CI's `full_refresh` instead of in the
~1s loop.

The property each test holds is the one its check exists for, so every case
below has a failing half. A check that only ever sees healthy input is the same
shape as a check nobody registered — green, and measuring nothing.

`AssetChecksDefinition` is callable, and none of these seven take a `context`,
so they need no execution harness: point the module's `DUCKDB_PATH` at a
throwaway file, call the check, read the `AssetCheckResult`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from orchestration.resources import dbt_project

# Same guard as `tests/test_definitions.py`: `just test` runs before
# `dbt deps && dbt parse` in ci.yml, and importing `orchestration.assets` needs
# the manifest that parse writes. CI re-runs this file after the parse step.
pytestmark = pytest.mark.skipif(
    not dbt_project.manifest_path.exists(),
    reason="needs dbt/target/manifest.json — run `just dbt-deps` and `dbt parse` first",
)

# The dlt-pipeline-deactivation fixture this file needs (importing the
# orchestration layer leaves a dlt pipeline active process-wide) lives in
# `tests/conftest.py`, shared with `test_definitions.py`.


@pytest.fixture(scope="module")
def assets():
    """The orchestration module, imported lazily so the skipif above can fire."""
    from orchestration import assets as module

    return module


def _warehouse(tmp_path: Path, *statements: str) -> str:
    """A throwaway DuckDB file built from `statements`, returned as a path str."""
    path = tmp_path / "warehouse.duckdb"
    con = duckdb.connect(str(path))
    try:
        for statement in statements:
            con.execute(statement)
    finally:
        con.close()
    return str(path)


def _meta(result, key):
    """The plain Python value behind a `MetadataValue` on an `AssetCheckResult`."""
    value = result.metadata[key]
    return getattr(value, "value", value)


def _sql_literal(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, str):
        return f"'{v}'"
    return str(v)


def _values_clause(rows: list[tuple]) -> str:
    return ", ".join("(" + ", ".join(_sql_literal(v) for v in r) + ")" for r in rows)


# --------------------------------------------------------------------------- #
# raw/wb_wdi — wdi_indicators_all_present
# --------------------------------------------------------------------------- #


def test_wdi_check_passes_when_every_configured_indicator_landed(tmp_path, monkeypatch, assets):
    path = _warehouse(
        tmp_path,
        "create schema raw",
        "create table raw.wb_wdi (indicator varchar)",
        "insert into raw.wb_wdi values ('NY.GDP.MKTP.KD'), ('NY.GDP.MKTP.KD'), ('SP.POP.TOTL')",
    )
    monkeypatch.setattr(assets, "DUCKDB_PATH", path)
    monkeypatch.setattr(assets, "WB_WDI_INDICATORS", ("NY.GDP.MKTP.KD", "SP.POP.TOTL"))

    result = assets.wdi_indicators_all_present()

    assert result.passed
    assert _meta(result, "indicators_loaded") == 2


def test_wdi_check_names_the_indicator_the_world_bank_answered_empty(tmp_path, monkeypatch, assets):
    """A bad indicator code comes back 200 with an empty series, not an error.

    The table is non-empty and every other indicator is fine, so nothing in the
    load fails — the column just arrives all-null in `stg_wdi`.
    """
    path = _warehouse(
        tmp_path,
        "create schema raw",
        "create table raw.wb_wdi (indicator varchar)",
        "insert into raw.wb_wdi values ('NY.GDP.MKTP.KD')",
    )
    monkeypatch.setattr(assets, "DUCKDB_PATH", path)
    monkeypatch.setattr(assets, "WB_WDI_INDICATORS", ("NY.GDP.MKTP.KD", "EN.ATM.CO2E.PC"))

    result = assets.wdi_indicators_all_present()

    assert not result.passed
    assert _meta(result, "missing_indicators") == ["EN.ATM.CO2E.PC"]


# --------------------------------------------------------------------------- #
# marts/fct_emissions_energy — mart_covers_recent_years
# --------------------------------------------------------------------------- #

MART_DDL = """
create table marts.fct_emissions_energy (
    year integer,
    co2_mt double,
    primary_energy_twh double,
    gdp_constant_usd double,
    electricity_price_eur_kwh double
)
"""


def _mart(tmp_path: Path, rows: list[tuple]) -> str:
    return _warehouse(
        tmp_path,
        "create schema marts",
        MART_DDL,
        f"insert into marts.fct_emissions_energy values {_values_clause(rows)}",
    )


def test_mart_check_passes_when_every_source_is_current(tmp_path, monkeypatch, assets):
    year = datetime.now(UTC).year
    path = _mart(tmp_path, [(year, 1.0, 2.0, 3.0, 0.25)])
    monkeypatch.setattr(assets, "DUCKDB_PATH", path)

    result = assets.mart_covers_recent_years()

    assert result.passed
    assert _meta(result, "years_behind") == dict.fromkeys(
        ("owid_co2", "owid_energy", "wb_wdi", "eu_elec_prices"), 0
    )


def test_mart_check_fails_a_source_with_no_rows_at_all(tmp_path, monkeypatch, assets):
    """`max(year)` over an all-null column is null, and null is the worst case.

    The obvious spelling — `lag <= 2` on a null lag — is not merely wrong, it is
    wrong in the direction that passes: a source that vanished entirely would
    score as healthy while a source two years late scored as broken.
    """
    year = datetime.now(UTC).year
    path = _mart(tmp_path, [(year, 1.0, 2.0, 3.0, None)])
    monkeypatch.setattr(assets, "DUCKDB_PATH", path)

    result = assets.mart_covers_recent_years()

    assert not result.passed
    assert _meta(result, "years_behind")["eu_elec_prices"] is None
    assert _meta(result, "max_year_by_source")["eu_elec_prices"] is None


def test_mart_check_passes_a_source_exactly_two_years_behind(tmp_path, monkeypatch, assets):
    """The check's own docstring promises "within two years", so `lag == 2` is
    the boundary case neither test above reaches: one has every source current
    (`lag == 0`), the other has a source entirely absent (`lag is None`).
    Mutating `lag <= 2` to `lag < 2` would pass both existing tests and fail
    only this one.
    """
    year = datetime.now(UTC).year
    path = _mart(
        tmp_path,
        [
            (year, 1.0, 2.0, 3.0, None),
            (year - 2, None, None, None, 0.25),
        ],
    )
    monkeypatch.setattr(assets, "DUCKDB_PATH", path)

    result = assets.mart_covers_recent_years()

    assert result.passed
    assert _meta(result, "years_behind")["eu_elec_prices"] == 2


def test_mart_check_fails_a_source_three_years_behind(tmp_path, monkeypatch, assets):
    """One year past the boundary above, the same source must fail."""
    year = datetime.now(UTC).year
    path = _mart(
        tmp_path,
        [
            (year, 1.0, 2.0, 3.0, None),
            (year - 3, None, None, None, 0.25),
        ],
    )
    monkeypatch.setattr(assets, "DUCKDB_PATH", path)

    result = assets.mart_covers_recent_years()

    assert not result.passed
    assert _meta(result, "years_behind")["eu_elec_prices"] == 3


# --------------------------------------------------------------------------- #
# marts/fct_fx_rates_daily — fx_rates_reach_the_present
# --------------------------------------------------------------------------- #


def _fx(tmp_path: Path, newest) -> str:
    return _warehouse(
        tmp_path,
        "create schema marts",
        "create table marts.fct_fx_rates_daily (rate_source_date date)",
        f"insert into marts.fct_fx_rates_daily values ('{newest.isoformat()}')",
    )


def test_fx_check_passes_across_a_christmas_length_gap(tmp_path, monkeypatch, assets):
    """The ECB closes for up to five consecutive days; that is not a failure."""
    newest = datetime.now(UTC).date() - timedelta(days=5)
    monkeypatch.setattr(assets, "DUCKDB_PATH", _fx(tmp_path, newest))

    result = assets.fx_rates_reach_the_present()

    assert result.passed
    assert _meta(result, "days_behind") == 5


def test_fx_check_fails_once_the_carry_forward_window_has_lapsed(tmp_path, monkeypatch, assets):
    """Past the cap every dense row for today is null, not stale — a conversion
    stops producing numbers rather than producing wrong ones, which is why this
    is measured in days where `mart_covers_recent_years` is measured in years."""
    newest = datetime.now(UTC).date() - timedelta(days=assets.FX_STALE_AFTER_DAYS + 1)
    monkeypatch.setattr(assets, "DUCKDB_PATH", _fx(tmp_path, newest))

    result = assets.fx_rates_reach_the_present()

    assert not result.passed
    assert _meta(result, "days_behind") == assets.FX_STALE_AFTER_DAYS + 1


def test_fx_check_warns_rather_than_blocks(tmp_path, monkeypatch, assets):
    import dagster as dg

    newest = datetime.now(UTC).date() - timedelta(days=90)
    monkeypatch.setattr(assets, "DUCKDB_PATH", _fx(tmp_path, newest))

    assert assets.fx_rates_reach_the_present().severity is dg.AssetCheckSeverity.WARN


# --------------------------------------------------------------------------- #
# analytics/co2_intensity — co2_intensity_rank_is_dense
# --------------------------------------------------------------------------- #


def _ranks(tmp_path: Path, ranks: list[int]) -> str:
    values = ", ".join(f"('High income', 2020, {r})" for r in ranks)
    return _warehouse(
        tmp_path,
        "create schema analytics",
        """
        create table analytics.co2_intensity (
            income_group varchar, year integer, co2_intensity_rank integer
        )
        """,
        f"insert into analytics.co2_intensity values {values}",
    )


def test_rank_check_passes_on_a_dense_cohort(tmp_path, monkeypatch, assets):
    monkeypatch.setattr(assets, "DUCKDB_PATH", _ranks(tmp_path, [1, 2, 3]))
    assert assets.co2_intensity_rank_is_dense().passed


def test_rank_check_fails_on_a_gap(tmp_path, monkeypatch, assets):
    """`rank()` leaves holes after a tie where `dense_rank()` does not — 1, 2, 2, 4."""
    monkeypatch.setattr(assets, "DUCKDB_PATH", _ranks(tmp_path, [1, 2, 2, 4]))

    result = assets.co2_intensity_rank_is_dense()

    assert not result.passed
    assert _meta(result, "bad_cohorts") == 1


def test_rank_check_fails_a_cohort_that_does_not_start_at_one(tmp_path, monkeypatch, assets):
    """A cohort ranked 2, 3, 4 has no gaps at all and is still wrong.

    The check states this as two terms, and the first of them is dead code:
    deleting `min(co2_intensity_rank) <> 1` leaves this test green, because
    `max(...) <> count(distinct ...)` already catches the case. That is not a
    weak fixture, it is a redundancy — if `max == count(distinct) == k` then the
    k distinct values are positive integers all <= k, so they are exactly
    {1..k} and the minimum is necessarily 1. Brute-forced over every multiset
    drawn from 1..8 up to length 6: no input reaches the first term.

    The clause stays because it states the intent, and this test stays because
    the *behaviour* is what matters. Recorded so the next reader does not spend
    the afternoon writing a fixture that cannot exist.
    """
    monkeypatch.setattr(assets, "DUCKDB_PATH", _ranks(tmp_path, [2, 3, 4]))
    assert not assets.co2_intensity_rank_is_dense().passed


# --------------------------------------------------------------------------- #
# analytics/retail_rfm — rfm_scores_do_not_split_ties
# --------------------------------------------------------------------------- #

RFM_DDL = """
create table analytics.retail_rfm (
    frequency integer, frequency_score integer,
    recency_days integer, recency_score integer,
    monetary_gbp double, monetary_score integer,
    rfm_cell varchar, rfm_total integer, segment varchar
)
"""


def _rfm(tmp_path: Path, rows: list[tuple]) -> str:
    return _warehouse(
        tmp_path,
        "create schema analytics",
        RFM_DDL,
        f"insert into analytics.retail_rfm values {_values_clause(rows)}",
    )


# (frequency, f_score, recency_days, r_score, monetary, m_score, cell, total, segment)
_TIED_PAIR = [
    (4, 3, 10, 5, 100.0, 4, "534", 12, "Loyal"),
    (4, 3, 20, 4, 250.0, 5, "435", 12, "Champions"),
]


def test_rfm_check_passes_when_equal_values_score_equally(tmp_path, monkeypatch, assets):
    monkeypatch.setattr(assets, "DUCKDB_PATH", _rfm(tmp_path, _TIED_PAIR))
    assert assets.rfm_scores_do_not_split_ties().passed


def test_rfm_check_fails_the_ntile_regression(tmp_path, monkeypatch, assets):
    """`ntile(5)` fills equal-sized buckets, so it cuts through a run of equal
    values: two customers with the same frequency land in different quintiles.

    The give-away is only this check. A regression to `ntile` still produces
    five tidy buckets and a plausible segment mix, so nothing else notices.
    """
    split = [_TIED_PAIR[0], (*_TIED_PAIR[1][:1], 2, *_TIED_PAIR[1][2:])]
    monkeypatch.setattr(assets, "DUCKDB_PATH", _rfm(tmp_path, split))

    result = assets.rfm_scores_do_not_split_ties()

    assert not result.passed
    assert _meta(result, "customers_scored_against_a_peer") == 2


def test_rfm_check_fails_a_null_that_does_not_travel_with_its_money(tmp_path, monkeypatch, assets):
    """Monetary is the one axis that legitimately goes null, and `rfm_cell` and
    `rfm_total` must go null exactly where it does. Asserting the equality is
    what stops this becoming a licence for nulls anywhere."""
    inconsistent = [(4, 3, 10, 5, None, 4, "534", 12, "Loyal")]
    monkeypatch.setattr(assets, "DUCKDB_PATH", _rfm(tmp_path, inconsistent))

    result = assets.rfm_scores_do_not_split_ties()

    assert not result.passed
    assert _meta(result, "scores_null_where_they_should_not_be") == 1


def test_rfm_check_fails_an_unsegmented_customer(tmp_path, monkeypatch, assets):
    unsegmented = [(*_TIED_PAIR[0][:8], None)]
    monkeypatch.setattr(assets, "DUCKDB_PATH", _rfm(tmp_path, unsegmented))

    result = assets.rfm_scores_do_not_split_ties()

    assert not result.passed
    assert _meta(result, "unsegmented") == 1


# --------------------------------------------------------------------------- #
# raw/om_weather_daily — weather_revisions_are_derivable
# --------------------------------------------------------------------------- #
#
# The two checks this replaces are gone with the layer they guarded:
# `lake_matches_warehouse` compared a hand-written Parquet copy against the
# warehouse, and there is no copy any more, and `lakehouse_matches_warehouse`
# caught an upsert whose prune failed, which dlt now performs inside one load
# package. What is newly fragile is the *substitute for the change feed* — see
# the check's own docstring.


def _lakehouse(tmp_path: Path, loads: list[list[tuple]]):
    """A DuckLake holding `raw.om_weather_daily`, written once per entry in `loads`.

    Written through plain SQL rather than through dlt, because what is under
    test is the diff and not the loader — and a dlt run per load would put a
    network-shaped dependency into a unit test. The `_dlt_*` columns are set to
    a fresh value on every write, which is exactly what dlt does and exactly
    what the diff has to ignore.
    """
    from modern_data_stack.ducklake import attach

    lake_dir = tmp_path / "lh"
    (lake_dir / "data").mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    attach(con, lake_dir / "catalog.duckdb", lake_dir / "data", alias="lakehouse")
    con.execute("create schema if not exists lakehouse.raw")
    for n, rows in enumerate(loads):
        values = ", ".join(
            f"('{iso}', date '{day}', {temp}, 'load_{n}', 'id_{n}_{i}')"
            for i, (iso, day, temp) in enumerate(rows)
        )
        con.execute("drop table if exists lakehouse.raw.om_weather_daily")
        con.execute(
            "create table lakehouse.raw.om_weather_daily as "
            "select * from (values " + values + ") as t"
            "(country_iso3, weather_date, temperature_2m_mean, _dlt_load_id, _dlt_id)"
        )
    con.close()
    return lake_dir


DAY1 = [("DEU", "2021-12-20", 3.5), ("FRA", "2021-12-20", 7.1)]


def test_weather_check_passes_when_nothing_was_restated(tmp_path, monkeypatch, assets):
    """The load that rewrites every `_dlt_id` and changes no weather.

    This is the routine case — every ingest re-merges 3,690 rows — and it is the
    one `ducklake_table_changes()` gets wrong, reporting the whole window as
    revised. Zero is the right answer and the check must see it as healthy.
    """
    lake_dir = _lakehouse(tmp_path, [DAY1, DAY1])
    monkeypatch.setattr(assets, "LAKEHOUSE_DIR", str(lake_dir))

    result = assets.weather_revisions_are_derivable()

    assert result.passed
    assert _meta(result, "rows_revised") == 0


def test_weather_check_passes_a_genuine_restatement(tmp_path, monkeypatch, assets):
    """One temperature moves. The diff must find exactly that row."""
    restated = [("DEU", "2021-12-20", -0.5), ("FRA", "2021-12-20", 7.1)]
    lake_dir = _lakehouse(tmp_path, [DAY1, restated])
    monkeypatch.setattr(assets, "LAKEHOUSE_DIR", str(lake_dir))

    result = assets.weather_revisions_are_derivable()

    assert result.passed
    assert _meta(result, "rows_revised") == 1


def test_weather_check_fails_when_every_row_reads_as_revised(tmp_path, monkeypatch, assets):
    """The failure the check exists for, and it is not an error anywhere else.

    Drop a provenance column from the ignore list and the diff stops being a
    restatement log: every row differs on `_dlt_id` alone, so it reports the
    whole table. That returns a *plausible* number rather than raising, which is
    why a bound over the total is the only thing that catches it. Simulated by
    restating every row, which is indistinguishable from the bug and is the
    thing the bound is actually asserting cannot happen.
    """
    all_moved = [("DEU", "2021-12-20", -9.9), ("FRA", "2021-12-20", -9.9)]
    lake_dir = _lakehouse(tmp_path, [DAY1, all_moved])
    monkeypatch.setattr(assets, "LAKEHOUSE_DIR", str(lake_dir))

    result = assets.weather_revisions_are_derivable()

    assert not result.passed
    assert _meta(result, "rows_revised") == _meta(result, "rows_total")


def test_weather_check_is_green_on_a_first_load(tmp_path, monkeypatch, assets):
    """Every CI run is a first load, and a single-version catalog has nothing to
    diff. That is the honest state, not a broken build — the same shape as the
    restatements page rendering its "nothing revised yet" branch."""
    lake_dir = _lakehouse(tmp_path, [DAY1])
    monkeypatch.setattr(assets, "LAKEHOUSE_DIR", str(lake_dir))

    result = assets.weather_revisions_are_derivable()

    assert result.passed
    assert _meta(result, "versions") <= 1


# --------------------------------------------------------------------------- #
# reports/evidence_site — site_pages_all_rendered
# --------------------------------------------------------------------------- #


def _routes(tmp_path: Path, sizes: dict[str, int | None]) -> dict[str, Path]:
    """`{slug: path}`, writing a file of `size` bytes where size is not None."""
    routes = {}
    for slug, size in sizes.items():
        path = tmp_path / slug / "index.html"
        if size is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x" * size)
        routes[slug] = path
    return routes


def test_site_check_passes_when_every_page_rendered(tmp_path, monkeypatch, assets):
    routes = _routes(tmp_path, {"index": 19_000, "retail": 92_000})
    monkeypatch.setattr(assets, "page_routes", lambda: routes)

    result = assets.site_pages_all_rendered()

    assert result.passed
    assert _meta(result, "pages_expected") == 2


def test_site_check_fails_a_page_that_never_rendered(tmp_path, monkeypatch, assets):
    """`evidence build` exits 0 for a site missing a page."""
    routes = _routes(tmp_path, {"index": 19_000, "retail": None})
    monkeypatch.setattr(assets, "page_routes", lambda: routes)

    result = assets.site_pages_all_rendered()

    assert not result.passed
    assert _meta(result, "missing") == ["retail"]


def test_site_check_fails_a_route_that_emitted_only_the_shell(tmp_path, monkeypatch, assets):
    """Present and non-empty, and still not a page. This is the failure that
    looks most like success, which is why the check measures size at all."""
    routes = _routes(tmp_path, {"index": 19_000, "retail": 900})
    monkeypatch.setattr(assets, "page_routes", lambda: routes)

    result = assets.site_pages_all_rendered()

    assert not result.passed
    assert _meta(result, "suspiciously_small") == {"retail": 900}
