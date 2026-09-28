"""An in-memory `marts.fct_retail_order_line`, for the tests of `agent/`.

A module of its own rather than a helper in `test_bridge.py`, because two test
files build one: the bridge's hand-worked values, and the agent loop's run over
the real tool. Only the columns `agent/bridge.py` reads exist. The EUR rates are
powers-of-two fractions so hand-worked values stay exact; USD is GBP × 1.5.
"""

from __future__ import annotations

import datetime as dt

import duckdb

RATE = {2021: 1.25, 2022: 1.125}


def sale(day: str, sku: str, units: int, gbp: float) -> tuple:
    return (day, "sale", "product", sku, True, units, gbp)


def cancel(day: str, sku: str, units: int, gbp: float) -> tuple:
    return (day, "cancellation", "product", sku, True, units, gbp)


def fee(day: str, gbp: float) -> tuple:
    return (day, "cancellation", "fee", "BANK CHARGES", False, -1, gbp)


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
