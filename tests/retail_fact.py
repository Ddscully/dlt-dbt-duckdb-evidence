"""A `marts.fct_retail_order_line` and `marts.dim_country`, for the tests of `agent/`.

A module of its own rather than a helper in `test_bridge.py`, because four test
files build one: the bridge's hand-worked values, the agent loop's run over the
real tool, the metrics', and the MCP server's. Only the columns `agent/` and MetricFlow's SQL read
exist. The EUR rates are powers-of-two fractions so hand-worked values stay
exact; USD is GBP × 1.5.

The database is named `warehouse`, because MetricFlow's SQL names its tables
`"warehouse"."marts".…`, after the real file.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb

RATE = {2021: 1.25, 2022: 1.125}


COUNTRIES = [
    ("GBR", "United Kingdom", "Europe & Central Asia"),
    ("FRA", "France", "Europe & Central Asia"),
    ("USA", "United States", "North America"),
]


def sale(
    day: str, sku: str, units: int, gbp: float, *, invoice=None, customer=None, country="GBR"
) -> tuple:
    return (day, "sale", "product", sku, True, units, gbp, invoice, customer, country)


def cancel(
    day: str, sku: str, units: int, gbp: float, *, invoice=None, customer=None, country="GBR"
) -> tuple:
    return (day, "cancellation", "product", sku, True, units, gbp, invoice, customer, country)


def fee(day: str, gbp: float) -> tuple:
    return (day, "cancellation", "fee", "BANK CHARGES", False, -1, gbp, None, None, "GBR")


def warehouse(lines: list[tuple], path: Path | None = None) -> duckdb.DuckDBPyConnection:
    """The fact, in memory, or with `path` (a file named `warehouse.duckdb`) on disk.

    A file is for a test that needs a second process to see the database: the
    lock is across processes. The caller closes the connection it gets back.
    """
    if path is None:
        con = duckdb.connect()
        con.execute("attach ':memory:' as warehouse; use warehouse")
    else:
        assert path.name == "warehouse.duckdb", "MetricFlow's SQL names the catalog `warehouse`"
        con = duckdb.connect(str(path))
    con.execute("create schema marts")
    con.execute(
        """create table marts.fct_retail_order_line (
            invoice_date date, invoice_type varchar, item_type varchar,
            stock_code varchar, is_revenue_line boolean, quantity bigint,
            line_amount_gbp double, year integer, is_stock_write_off boolean,
            line_amount_eur double, line_amount_usd double,
            invoice varchar, line_number bigint, customer_id varchar, country_iso3 varchar)"""
    )
    con.execute(
        "create table marts.dim_country (country_iso3 varchar, country_name varchar, region varchar)"
    )
    con.executemany("insert into marts.dim_country values (?, ?, ?)", COUNTRIES)
    for n, line in enumerate(lines):
        day, invoice_type, item_type, sku, revenue, units, gbp, invoice, customer, country = line
        year = dt.date.fromisoformat(day).year
        con.execute(
            "insert into marts.fct_retail_order_line values (?, ?, ?, ?, ?, ?, ?, ?, false, ?, ?, ?, ?, ?, ?)",
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
                invoice or f"L{n}",  # one invoice per line unless the test says otherwise
                n,
                customer,
                country,
            ],
        )
    return con
