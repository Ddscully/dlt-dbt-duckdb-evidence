"""Reads and writes against a DuckDB connection, with the Optional taken off.

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

`qualify` is here for the same reason as `write_frames` below, one module
later: naming a relation across an *attached* catalog stopped being one module's
business the day this project started keeping two catalogs open at once, and a
three-line helper copied into the second caller is the shape the rule is about.

`write_frames` is here for a different reason: it is the one write shape this
project repeats — register a Polars frame, `create or replace`, unregister — and
it lived in `observability` because that is where it was first needed. Writing a
carbon metric by importing a module about dbt/dlt metadata reads wrong, so the
general operation sits in the general module and `observability` is back to
being about metadata.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import duckdb
import polars as pl


def qualify(database: str | None, schema: str, table: str) -> str:
    """`"db"."schema"."table"`, or `"schema"."table"` when `database` is None.

    The database half is not decoration and not always optional.
    `information_schema` spans every attached catalog, so a query filtered on the
    schema alone matches a `raw` in *either* one — and since raw landed in
    DuckLake this project genuinely runs with two catalogs attached, one of which
    may still hold a stale `raw` from before the move. Naming the catalog is what
    makes the read say which one it meant.
    """
    prefix = "" if database is None else f'"{database}".'
    return f'{prefix}"{schema}"."{table}"'


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


def write_frames(
    con: duckdb.DuckDBPyConnection,
    frames: dict[str, pl.DataFrame],
    schema: str,
) -> dict[str, int]:
    """Write each frame to `<schema>.<name>`, replacing it. Returns rows written.

    Takes a connection rather than a path: the frames were read through one, and
    DuckDB allows a single writer, so re-opening the file here would be a lock to
    trip over for no benefit.

    `schema` has no default. The three callers all write `analytics` today, which
    is exactly what would make a default invisible — a fourth caller meaning some
    other schema would get this one by omission, and `create or replace` does not
    ask twice.
    """
    con.sql(f"create schema if not exists {schema}")
    for name, frame in frames.items():
        con.register("frame_df", frame)  # DuckDB reads Polars frames directly
        con.sql(f"create or replace table {schema}.{name} as select * from frame_df")
        # Neither of the two hand-rolled copies this replaced unregistered, so a
        # long-lived connection kept the last frame alive and a second write
        # silently rebound the same name.
        con.unregister("frame_df")
    return {name: frame.height for name, frame in frames.items()}
