"""Unit tests for the Polars derived-metrics layer.

`build_co2_intensity` and `build_retail_rfm` are pure frame-in/frame-out
functions, so they're tested directly with hand-built frames — no DuckDB, no
warehouse.
"""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from transform.co2_intensity import build_co2_intensity
from transform.retail_rfm import SEGMENT_GRID, assign_quintiles, build_retail_rfm

SCHEMA = {
    "country_iso3": pl.Utf8,
    "year": pl.Int64,
    "income_group": pl.Utf8,
    "co2_mt": pl.Float64,
    "gdp_constant_usd": pl.Float64,
}


def _row(iso3: str, year: int, income_group: str, co2_mt: float, gdp: float | None) -> dict:
    return {
        "country_iso3": iso3,
        "year": year,
        "income_group": income_group,
        "co2_mt": co2_mt,
        "gdp_constant_usd": gdp,
    }


def _frame(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=SCHEMA)


def test_intensity_converts_megatonnes_to_kilograms():
    """co2_mt is million tonnes and the metric is kg per dollar, so the
    numerator carries a 1e9 factor. 1 Mt over $1bn is exactly 1 kg/$."""
    out = build_co2_intensity(_frame([_row("AAA", 2020, "High income", 1.0, 1e9)]))
    assert out["co2_per_gdp_const_usd"][0] == pytest.approx(1.0)


def test_intensity_uses_constant_price_gdp():
    """The column read must be `gdp_constant_usd`.

    Dividing by current-US$ GDP is the bug this metric was fixed for: it made
    Japan's 21% emissions cut score 10% *worse* purely on a falling yen. A frame
    carrying a wildly different `gdp_usd` must not change the answer.
    """
    df = _frame([_row("JPN", 2024, "High income", 2.0, 4e9)]).with_columns(
        pl.lit(1e9).alias("gdp_usd")
    )
    assert build_co2_intensity(df)["co2_per_gdp_const_usd"][0] == pytest.approx(0.5)


@pytest.mark.parametrize("gdp", [None, 0.0, -1.0])
def test_rows_without_usable_gdp_are_dropped(gdp):
    """Null, zero and negative denominators all drop out rather than producing
    an infinity that would then win the rank."""
    out = build_co2_intensity(
        _frame(
            [
                _row("AAA", 2020, "Low income", 1.0, gdp),
                _row("BBB", 2020, "Low income", 1.0, 1e9),
            ]
        )
    )
    assert out["country_iso3"].to_list() == ["BBB"]


def test_rank_is_dense_and_per_cohort():
    """Ranking restarts within each (income_group, year) and ties share a rank
    without leaving a gap — the property `co2_intensity_rank_is_dense` asserts
    on the real table."""
    out = build_co2_intensity(
        _frame(
            [
                # High income, 2020: two countries tied at the top, one above
                _row("AAA", 2020, "High income", 1.0, 1e9),
                _row("BBB", 2020, "High income", 1.0, 1e9),
                _row("CCC", 2020, "High income", 3.0, 1e9),
                # different cohorts, which must each start again from 1
                _row("DDD", 2020, "Low income", 9.0, 1e9),
                _row("EEE", 2021, "High income", 9.0, 1e9),
            ]
        )
    )
    ranks = dict(zip(out["country_iso3"], out["co2_intensity_rank"]))
    assert ranks["AAA"] == ranks["BBB"] == 1
    assert ranks["CCC"] == 2  # dense: the tie doesn't push this to 3
    assert ranks["DDD"] == 1  # new income group
    assert ranks["EEE"] == 1  # new year


# --------------------------------------------------------------------------- #
# RFM — `transform/retail_rfm.py`
# --------------------------------------------------------------------------- #

CUSTOMER_SCHEMA = {
    "customer_id": pl.Utf8,
    "country": pl.Utf8,
    "country_iso3": pl.Utf8,
    "cohort_month": pl.Utf8,
    "first_order_date": pl.Date,
    "last_order_date": pl.Date,
    "n_orders": pl.Int64,
    "net_revenue_gbp": pl.Float64,
    "avg_order_value_gbp": pl.Float64,
    "n_distinct_products": pl.Int64,
    "return_rate_pct": pl.Float64,
    "is_left_censored_cohort": pl.Boolean,
}


def _customer(cid: str, last_order: str, n_orders: int, revenue: float) -> dict:
    return {
        "customer_id": cid,
        "country": "United Kingdom",
        "country_iso3": "GBR",
        "cohort_month": "2010-01",
        "first_order_date": dt.date(2010, 1, 1),
        "last_order_date": dt.date.fromisoformat(last_order),
        "n_orders": n_orders,
        "net_revenue_gbp": revenue,
        "avg_order_value_gbp": revenue / n_orders,
        "n_distinct_products": n_orders * 3,
        "return_rate_pct": 0.0,
        "is_left_censored_cohort": False,
    }


def _customers(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=CUSTOMER_SCHEMA)


def test_quintiles_score_by_value_so_ties_never_split():
    """The property the whole module exists for, and the one `ntile` breaks.

    Twelve rows over three distinct values: `ntile(5)` would cut them into five
    buckets of two or three regardless of where the values change, so a run of
    equal values lands in more than one. `qcut` cuts on the break points, so a
    value maps to exactly one score.
    """
    values = pl.Series("frequency", [1] * 6 + [2] * 3 + [9] * 3)
    scores = assign_quintiles(values)
    by_value = dict(zip(values, scores))
    # one score per distinct value, and the run of six 1s is not split
    assert len({s for v, s in zip(values, scores) if v == 1}) == 1
    assert by_value[1] < by_value[2] < by_value[9]


def test_quintiles_reverse_for_recency():
    """Recency is days-since, so *small* is good and the score has to invert —
    otherwise the most engaged customers score 1 and every segment is mirrored."""
    values = pl.Series("recency_days", [0, 100, 200, 300, 400])
    ascending = assign_quintiles(values)
    descending = assign_quintiles(values, higher_is_better=False)
    assert ascending.to_list() == [1, 2, 3, 4, 5]
    assert descending.to_list() == [5, 4, 3, 2, 1]
    # and the inversion is exact, not an approximation of one
    assert (ascending + descending).unique().to_list() == [6]


def test_every_cell_of_the_grid_is_named_exactly_once():
    """25 cells, no gaps and no duplicates — the reason the segment map is a
    grid rather than the usual list of overlapping `R>=4 and F>=4` rules, whose
    answer depends on the order the branches happen to be written in."""
    assert len(SEGMENT_GRID) == 25
    assert set(SEGMENT_GRID) == {(r, f) for r in range(1, 6) for f in range(1, 6)}


def test_no_customer_comes_out_unsegmented():
    """A left join against the grid is only safe because the grid is complete;
    a missing cell would show up as a null segment rather than an error."""
    rows = [
        _customer(f"C{i:03d}", f"2011-{1 + i % 12:02d}-01", 1 + i % 40, 100.0 * (1 + i % 40))
        for i in range(120)
    ]
    out = build_retail_rfm(_customers(rows), dt.date(2011, 12, 9))
    assert out["segment"].null_count() == 0
    assert set(out["segment"].unique()) <= set(SEGMENT_GRID.values())


def test_recency_is_measured_against_the_extract_not_today():
    """`as_of_date` is a required parameter for exactly this reason: against
    `date.today()` a 2011 extract makes every customer thousands of days lapsed
    and recency stops discriminating at all."""
    rows = [
        _customer("A", "2011-12-09", 5, 500.0),
        _customer("B", "2011-06-09", 5, 500.0),
    ]
    out = build_retail_rfm(_customers(rows), dt.date(2011, 12, 9)).sort("customer_id")
    assert out["recency_days"].to_list() == [0, 183]
    assert out["as_of_date"].to_list() == [dt.date(2011, 12, 9)] * 2


def test_monetary_is_net_so_a_heavy_returner_scores_low():
    """Gross revenue would rank a customer who sent almost everything back
    alongside one who kept it."""
    rows = [
        _customer("KEEPER", "2011-12-01", 10, 10_000.0),
        _customer("RETURNER", "2011-12-01", 10, -50.0),
    ]
    out = build_retail_rfm(_customers(rows), dt.date(2011, 12, 9))
    scores = dict(zip(out["customer_id"], out["monetary_score"]))
    assert scores["KEEPER"] > scores["RETURNER"]


def test_rfm_cell_is_text_and_total_is_arithmetic():
    """The cell is a label — "155" is not a number eleven times bigger than
    "55" — so the sortable version is a separate integer column."""
    out = build_retail_rfm(
        _customers([_customer("A", "2011-12-09", 3, 300.0)]), dt.date(2011, 12, 9)
    )
    assert out["rfm_cell"].dtype == pl.String
    row = out.row(0, named=True)
    assert (
        row["rfm_cell"] == f"{row['recency_score']}{row['frequency_score']}{row['monetary_score']}"
    )
    assert row["rfm_total"] == (
        row["recency_score"] + row["frequency_score"] + row["monetary_score"]
    )
