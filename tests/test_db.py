"""The read helpers in `modern_data_stack.db`.

Small surface, but two of its four behaviours are distinctions that are easy to
collapse by accident — "no row" against "a row holding NULL", and a raise that
names the query against the `TypeError` that `.fetchone()[0]` gives you from
whichever line happened to touch the None.
"""

from __future__ import annotations

import datetime as dt

import duckdb
import pytest

from modern_data_stack.db import row, scalar


@pytest.fixture
def con():
    con = duckdb.connect(":memory:")
    con.execute("create table t(a integer, d date)")
    con.execute("insert into t values (1, '2020-01-01'), (2, '2021-06-30')")
    try:
        yield con
    finally:
        con.close()


def test_scalar_reads_the_first_column(con):
    assert scalar(con, "select count(*) from t") == 2


def test_row_reads_every_column_in_order(con):
    assert row(con, "select min(a), max(d) from t") == (1, dt.date(2021, 6, 30))


def test_parameters_are_passed_through(con):
    assert scalar(con, "select count(*) from t where a > ?", [1]) == 1


def test_a_null_value_is_a_value_and_not_an_error(con):
    """`select max(a)` over an empty table returns one row holding None.

    That is the distinction the helpers exist to keep: the *row* is present, so
    there is nothing to raise about, and only the caller who asked for a max
    knows whether a null answer is a problem. Collapsing this into the no-row
    branch would make `_period_span` in observability.py raise on every
    dimension table it inventories.
    """
    con.execute("delete from t")
    assert row(con, "select max(a) from t") == (None,)
    assert scalar(con, "select max(a) from t") is None


def test_no_row_raises_and_names_the_query(con):
    """The bare idiom raises `TypeError: 'NoneType' object is not subscriptable`
    from the subscript, which names neither the query nor the connection."""
    con.execute("delete from t")
    with pytest.raises(ValueError, match=r"no rows: select a from t limit 1"):
        scalar(con, "select a from t limit 1")
