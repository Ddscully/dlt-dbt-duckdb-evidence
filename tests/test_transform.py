"""Unit tests for the Polars derived-metrics layer.

`build_co2_intensity` is a pure frame-in/frame-out function, so it's tested
directly with hand-built frames — no DuckDB, no warehouse.
"""

from __future__ import annotations

import polars as pl
import pytest

from transform.co2_intensity import build_co2_intensity

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
