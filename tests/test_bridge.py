"""The revenue bridge (`agent/bridge.py`) against a fact small enough to do by hand.

The identity "the bars sum to the change" holds for *any* volume and mix
formula, because price is computed as what is left over; so the test with
teeth is the one that checks each bar against a value worked out on paper.
The EUR rates are powers-of-two fractions so the hand values are exact too.
"""

from __future__ import annotations

import datetime as dt
from fractions import Fraction

import duckdb
import pytest

from agent.bridge import explain_change, render

RATE = {2021: 1.25, 2022: 1.125}


def sale(day: str, sku: str, units: int, gbp: float) -> tuple:
    return (day, "sale", "product", sku, True, units, gbp)


def cancel(day: str, sku: str, units: int, gbp: float) -> tuple:
    return (day, "cancellation", "product", sku, True, units, gbp)


def fee(day: str, gbp: float) -> tuple:
    return (day, "cancellation", "fee", "BANK CHARGES", False, -1, gbp)


# A and B sold in both years; C only in 2021, D only in 2022.
LINES = [
    sale("2021-01-01", "A", 10, 10.0),
    sale("2021-03-01", "B", 10, 20.0),
    sale("2021-03-01", "C", 4, 8.0),
    cancel("2021-04-01", "A", -1, -3.0),
    fee("2021-04-01", -1.0),
    sale("2022-03-01", "A", 12, 12.0),
    sale("2022-03-01", "B", 5, 12.5),
    sale("2022-12-31", "D", 2, 6.0),
    cancel("2022-04-01", "B", -1, -5.0),
    fee("2022-04-01", -2.0),
]


def warehouse(lines: list[tuple]) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("create schema marts")
    con.execute(
        """create table marts.fct_retail_order_line (
            invoice_date date, invoice_type varchar, item_type varchar,
            stock_code varchar, is_revenue_line boolean, quantity bigint,
            line_amount_gbp double, year integer, is_stock_write_off boolean,
            line_amount_eur double, line_amount_usd double)"""
    )
    for day, invoice_type, item_type, sku, revenue, units, gbp in lines:
        year = dt.date.fromisoformat(day).year
        con.execute(
            "insert into marts.fct_retail_order_line values (?, ?, ?, ?, ?, ?, ?, ?, false, ?, ?)",
            [
                day,
                invoice_type,
                item_type,
                sku,
                revenue,
                units,
                gbp,
                year,
                gbp * RATE[year],
                gbp * 1.5,
            ],
        )
    return con


def bars(bridge) -> dict[str, Fraction]:
    return {bar.name: bar.value for bar in bridge.bars}


def test_each_bar_matches_its_hand_value():
    # Continuing A+B: 20 units for £30 (average £1.50), then 17 units for
    # £24.50; 2022's units at 2021's prices are 12·£1 + 5·£2 = £22.
    gbp = explain_change(warehouse(LINES), 2021, 2022, "GBP")
    assert bars(gbp) == {
        "volume": Fraction(-9, 2),  # (17 − 20) · 1.5
        "mix": Fraction(-7, 2),  # 22 − 17 · 1.5
        "price": Fraction(5, 2),  # 24.5 − 22
        "new_skus": Fraction(6),  # D
        "discontinued_skus": Fraction(-8),  # C
        "returns": Fraction(-2),  # −5 − (−3)
    }
    eur = explain_change(warehouse(LINES), 2021, 2022, "EUR")
    # GBP effects at 2021's 1.25; FX is 2022's €28.6875 less 1.25 · £25.50.
    assert bars(eur) == {
        **{k: v * Fraction(5, 4) for k, v in bars(gbp).items()},
        "fx": Fraction(-51, 16),
    }
    assert eur.non_product.value == Fraction(-1)  # −2 · 1.125 + 1 · 1.25
    assert not eur.aligned


@pytest.mark.parametrize("currency", ["GBP", "EUR", "USD"])
def test_the_bars_sum_to_the_change_exactly(currency):
    bridge = explain_change(warehouse(LINES), 2021, 2022, currency)
    assert sum(bar.value for bar in bridge.bars) == bridge.change
    assert bridge.change + bridge.non_product.value == bridge.invoiced_b - bridge.invoiced_a


def test_a_short_year_is_compared_over_the_common_window():
    # The data now ends 2022-12-09, so 2021's 20 December sale is outside the
    # window both years are compared over.
    lines = [line for line in LINES if not line[0].startswith("2022-12")]
    lines += [sale("2022-12-09", "D", 2, 6.0), sale("2021-12-20", "A", 50, 100.0)]
    bridge = explain_change(warehouse(lines), 2021, 2022, "GBP")
    assert bridge.window == ((1, 1), (12, 9))
    assert bridge.net_revenue_a == 35  # the £100 sale is not in it
    assert bridge.unaligned_change == Fraction(255, 1350) - 1  # 25.5 / 135 − 1
    assert "compared over 1 Jan to 9 Dec" in render(bridge)
    assert "compared over" not in render(explain_change(warehouse(LINES), 2021, 2022))


def test_a_year_outside_the_data_is_refused():
    with pytest.raises(ValueError, match="outside the data"):
        explain_change(warehouse(LINES), 2021, 2023)
