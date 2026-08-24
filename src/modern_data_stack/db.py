"""Scalar and single-row reads from DuckDB, with the Optional taken off.

`DuckDBPyConnection.fetchone()` is typed `tuple[Any, ...] | None`, because in
general a query need not return a row. Almost none of the reads in this repo are
that general — they are `select count(*)`, `select max(year)`, `select
current_database()`, ungrouped aggregates that return exactly one row by
construction. So the code wrote `.fetchone()[0]` and moved on, in 40 places.

That was 23 of the 36 diagnostics `just typecheck` opened with, and every one of
them the same false alarm. The cost of leaving it was never the noise: it is that
23 false alarms in a 36-line report is how a checker stops being read, and the
thirteenth real one then arrives into a wall nobody scans any more.

So the invariant is stated once, here, instead of 40 times implicitly — and
stating it buys a real check as a side effect. `.fetchone()[0]` against a query
that unexpectedly returns nothing raises `TypeError: 'NoneType' object is not
subscriptable` from whichever line happened to touch it; `scalar()` raises
naming the query.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import duckdb


def row(
    con: duckdb.DuckDBPyConnection,
    sql: str,
    params: Sequence[Any] | None = None,
) -> tuple[Any, ...]:
    """The single row `sql` returns, as a tuple. Raises if it returned none.

    For the ungrouped aggregates this repo reads, "no row" cannot happen — which
    is exactly why it is worth raising on. Reaching it means the caller passed a
    query this helper does not cover (`… limit 1` over an empty table, a `group
    by` that matched nothing), and that is a bug at the call site rather than a
    value to propagate.

    A row holding NULL is a *different fact* and comes back normally: `select
    max(year)` over an empty table returns one row of `None`, and the caller who
    asked for a max is the one who knows whether that is an error.
    """
    result = con.execute(sql, params) if params is not None else con.execute(sql)
    fetched = result.fetchone()
    if fetched is None:
        raise ValueError(f"query returned no rows: {sql.strip()}")
    return fetched


def scalar(
    con: duckdb.DuckDBPyConnection,
    sql: str,
    params: Sequence[Any] | None = None,
) -> Any:
    """The first column of the single row `sql` returns.

    Deliberately `Any` rather than a generic narrowed by an `expect=int`
    argument. What comes out of DuckDB genuinely is dynamic — a `count(*)` is an
    int, a `max(order_date)` is a date, `current_database()` is a str — and
    making 23 call sites each name a type would be a second invariant to keep
    true, in exchange for narrowing that only the few callers who actually do
    arithmetic on the result need. Those callers narrow locally; see the
    `as_of_date` guard in `transform/retail_rfm.py`.
    """
    return row(con, sql, params)[0]
