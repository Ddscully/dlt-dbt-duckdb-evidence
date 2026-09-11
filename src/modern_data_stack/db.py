"""Reads and writes against a DuckDB connection.

`fetchone()` is typed `tuple | None`, but nearly every read here is an ungrouped
aggregate that returns exactly one row. `row` and `scalar` state that invariant
once, so the type checker stops flagging every call site, and raise naming the
query when it fails — where `.fetchone()[0]` would raise an anonymous
`TypeError`.

`qualify` names a relation across attached catalogs. `write_frames` (replace)
and `append_frame` (accumulate) are the two write shapes the project repeats.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import duckdb
import polars as pl


def qualify(database: str | None, schema: str, table: str) -> str:
    """`"db"."schema"."table"`, or `"schema"."table"` when `database` is None.

    Pass `database` whenever more than one catalog is attached: a bare schema
    name can resolve to a same-named schema in the wrong one.
    """
    prefix = "" if database is None else f'"{database}".'
    return f'{prefix}"{schema}"."{table}"'


def row(
    con: duckdb.DuckDBPyConnection,
    sql: str,
    params: Sequence[Any] | None = None,
) -> tuple[Any, ...]:
    """The single row `sql` returns, as a tuple. Raises if it returned none.

    No row means the caller passed a query this does not cover (`limit 1` over
    an empty table, a `group by` matching nothing) — a call-site bug. A row of
    NULLs is returned normally: `max(year)` over an empty table is one such row,
    and only the caller knows whether that is an error.
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

    Typed `Any` because the result genuinely varies (int, date, str); the few
    callers that do arithmetic narrow locally, as `transform/retail_rfm.py` does.
    """
    return row(con, sql, params)[0]


def write_frames(
    con: duckdb.DuckDBPyConnection,
    frames: dict[str, pl.DataFrame],
    schema: str,
) -> dict[str, int]:
    """Write each frame to `<schema>.<name>`, replacing it. Returns rows written.

    Takes the caller's connection: DuckDB allows one writer, so reopening the
    file would contend with it. `schema` has no default — every caller writing
    `analytics` would make a default invisible to one that means otherwise, and
    `create or replace` does not ask twice.
    """
    con.sql(f"create schema if not exists {schema}")
    for name, frame in frames.items():
        con.register("frame_df", frame)  # DuckDB reads Polars frames directly
        con.sql(f"create or replace table {schema}.{name} as select * from frame_df")
        # So a long-lived connection does not keep the last frame alive.
        con.unregister("frame_df")
    return {name: frame.height for name, frame in frames.items()}


def append_frame(
    con: duckdb.DuckDBPyConnection,
    frame: pl.DataFrame,
    schema: str,
    table: str,
    key: str,
) -> int:
    """Append `frame` to `<schema>.<table>`, skipping keys already there.

    For a history, which a replace would reduce to the last batch. Idempotent on
    `key` — reading the same artifact twice appends nothing — rather than on the
    whole row, which would start appending duplicates the moment any column
    became non-deterministic. `key` has no default: only the caller knows what
    identifies a batch.
    """
    con.sql(f"create schema if not exists {schema}")
    qualified = qualify(None, schema, table)
    con.register("append_df", frame)
    try:
        # Create the shape only (`limit 0`); the insert below adds every row, so
        # a first run cannot count its rows twice.
        con.sql(f"create table if not exists {qualified} as select * from append_df limit 0")
        before = scalar(con, f"select count(*) from {qualified}")
        # `by name`: a table carried from a previous release may have been
        # written with a different column order.
        con.sql(f"""
            insert into {qualified} by name
            select * from append_df
            where {key} not in (select {key} from {qualified})
        """)
        return scalar(con, f"select count(*) from {qualified}") - before
    finally:
        con.unregister("append_df")
