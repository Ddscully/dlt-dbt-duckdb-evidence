"""Turn the warehouse's own metadata into queryable tables.

Everything a pipeline like this knows about itself is already in the database —
dlt stamps `_dlt_load_id` on every raw row, dbt stores each failing test row in
its audit schema when `store_failures` is on, and `information_schema` knows the
shape of every layer. None of it is *queryable from a report*, because two of the
three need either dynamic SQL over a table list that isn't known until runtime or
a file outside the database. This module resolves both.

None of it is new instrumentation. A pipeline page costs a few dozen lines of SQL
over metadata three tools were already writing, which is worth knowing before
anyone proposes emitting metrics for it.

Landing tables and layer names come from the project; see
`transform/pipeline_status.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import polars as pl

from .db import qualify, row, scalar

DEFAULT_AUDIT_SCHEMA = "dbt_test__audit"

# dbt's own default: a test fails on the number of rows it returned. Tests are
# free to override it, and `build_tests` reads each one's from the manifest.
DEFAULT_FAIL_CALC = "count(*)"


def _has_column(
    con: duckdb.DuckDBPyConnection,
    schema: str,
    table: str,
    column: str,
    database: str | None = None,
) -> bool:
    """Whether `column` is on `<database>.<schema>.<table>`.

    `database` is optional because most callers query the connection's own
    catalog, and required in spirit for the one that does not: a landing schema
    in an attached DuckLake is `lakehouse.raw`, and `information_schema.columns`
    without the `table_catalog` filter happily matches a `raw` schema in *any*
    attached database. Two catalogs each holding a `raw` schema is exactly this
    project's shape.
    """
    params = {"schema": schema, "table": table, "column": column}
    clause = ""
    if database is not None:
        # Both halves together: DuckDB rejects a named parameter the statement
        # does not mention (`identifiers of the excess parameters: database`), so
        # the binding cannot be passed unconditionally alongside an optional
        # clause.
        clause = " and table_catalog = $database"
        params["database"] = database
    return bool(
        con.execute(
            f"""
            select 1 from information_schema.columns
            where table_schema = $schema and table_name = $table
              and column_name = $column{clause}
            """,
            params,
        ).fetchone()
    )


def _period_span(
    con: duckdb.DuckDBPyConnection,
    schema: str,
    table: str,
    column: str,
    database: str | None = None,
) -> tuple[int | None, int | None]:
    """(min, max) of a table's period column, or (None, None) if it has none.

    The return type says `| None` twice because both branches can produce it —
    the early return for a table with no period column, and `min()`/`max()` over
    a table that has one and no rows. It read `tuple[int, int]` until ty pointed
    at the line directly below the docstring that already said otherwise.
    """
    if not _has_column(con, schema, table, column, database):
        return (None, None)
    lo, hi = row(
        con, f"select min({column}), max({column}) from {qualify(database, schema, table)}"
    )
    return (lo, hi)


def build_sources(
    con: duckdb.DuckDBPyConnection,
    source_tables: tuple[str, ...],
    raw_schema: str = "raw",
    period_column: str = "year",
    raw_database: str | None = None,
) -> pl.DataFrame:
    """Row counts, period span and load time for each dlt landing table.

    Freshness comes from `_dlt_load_id`, which dlt stamps as a **unix epoch in a
    varchar column** — the same value `dbt source freshness` reads. It records
    when *this pipeline* loaded the data, not when the publisher released it, so
    a stale timestamp here means the pipeline stopped running, not that the
    upstream stopped publishing.
    """
    rows = []
    for table in source_tables:
        if not _has_column(con, raw_schema, table, "_dlt_load_id", raw_database):
            continue
        n, loaded_at = row(
            con,
            f"""
            select
                count(*),
                to_timestamp(max(cast(_dlt_load_id as double)))
            from {qualify(raw_database, raw_schema, table)}
            """,
        )
        period_min, period_max = _period_span(con, raw_schema, table, period_column, raw_database)
        rows.append(
            {
                "source_table": f"{raw_schema}.{table}",
                "rows": n,
                "year_min": period_min,
                "year_max": period_max,
                "loaded_at": loaded_at,
            }
        )
    return pl.DataFrame(rows).sort("source_table")


def build_tables(
    con: duckdb.DuckDBPyConnection,
    layers: tuple[str, ...],
    exclude_prefix: str = "pipeline_",
    period_column: str = "year",
) -> pl.DataFrame:
    """Row counts and period spans for every table in the modelled layers.

    `exclude_prefix` keeps this module's own output out of the inventory.
    Without it the inventory inventories itself, and the table count depends on
    whether it has run before — 10 on a first build, 13 on every later one, for
    no change in the warehouse.

    An empty `exclude_prefix` means "exclude nothing", and has to drop the
    predicate rather than pass it: `not like '' || '%'` is `not like '%'`, which
    matches no row at all. That returns an empty inventory, which surfaces much
    later and much less legibly as an empty frame out of `db.write_frames`.
    """
    predicate = ""
    params: dict[str, object] = {"layers": list(layers)}
    if exclude_prefix:
        predicate = "and table_name not like $prefix || '%' escape '\\'"
        params["prefix"] = exclude_prefix.replace("_", "\\_")

    listed = con.execute(
        f"""
        select table_schema, table_name
        from information_schema.tables
        where table_schema in (select unnest($layers))
          {predicate}
        order by table_schema, table_name
        """,
        params,
    ).fetchall()

    rows = []
    for schema, table in listed:
        n = scalar(con, f'select count(*) from "{schema}"."{table}"')
        period_min, period_max = _period_span(con, schema, table, period_column)
        rows.append(
            {
                "layer": schema,
                "table_name": f"{schema}.{table}",
                "rows": n,
                "year_min": period_min,
                "year_max": period_max,
            }
        )
    return pl.DataFrame(rows)


def tested_model_name(attached: str, nodes: dict[str, dict]) -> str | None:
    """Readable name for the node a test is attached to.

    `attached_node` is a unique id, and the last segment is only the model name
    while the model has no versions: a versioned one is
    `model.<project>.fct_emissions_energy.v1`, so splitting on the final dot
    labels the test **`v1`**. Prefer the node's own `alias`, which is the
    relation the test actually ran against (`fct_emissions_energy_v1`,
    `fct_emissions_energy`) and so matches what every other table on the
    pipeline page calls it. The split stays as the fallback, because a test can
    attach to a source, which does not live in `nodes`.
    """
    node = nodes.get(attached) or {}
    return node.get("alias") or attached.rsplit(".", 1)[-1] or None


def manifest_tests(manifest_path: str) -> dict[str, dict]:
    """Map audit-table name -> {test type, model it guards, column, fail_calc, severity}.

    The audit table is named after the test's `alias`, and dbt **truncates and
    hashes** an alias longer than 63 characters
    (`dbt_utils_accepted_range_fct_c_1c6718ee2bb...`), so the table name on its
    own is not a readable label. The manifest is where the real name, the
    attached model and the tested column live — and it's gitignored, so an absent
    manifest degrades to bare table names rather than failing.

    `fail_calc` and `severity` come from the same node and are what make a
    *passing* test read as passing — see `build_tests`.
    """
    path = Path(manifest_path)
    if not path.exists():
        return {}

    manifest = json.loads(path.read_text())
    nodes = manifest.get("nodes", {})
    out: dict[str, dict] = {}
    for node in nodes.values():
        if node.get("resource_type") != "test":
            continue
        metadata = node.get("test_metadata") or {}
        attached = node.get("attached_node") or ""
        config = node.get("config") or {}
        out[node["alias"]] = {
            "test_name": node["name"],
            "test_type": metadata.get("name") or "singular",
            "tested_model": tested_model_name(attached, nodes),
            "tested_column": (metadata.get("kwargs") or {}).get("column_name"),
            "fail_calc": config.get("fail_calc") or DEFAULT_FAIL_CALC,
            # dbt writes this as "ERROR"/"WARN", but a yml can spell it lowercase.
            "severity": (config.get("severity") or "error").lower(),
        }
    return out


def build_tests(
    con: duckdb.DuckDBPyConnection,
    manifest_path: str,
    audit_schema: str = DEFAULT_AUDIT_SCHEMA,
) -> pl.DataFrame:
    """One row per dbt test, with the number of rows currently failing it.

    Requires `+store_failures: true` project-wide, so each test leaves a table in
    the audit schema holding the rows it rejected. For most tests an empty table
    is a *passing* test — which is why this counts rather than checks existence,
    and why a green pipeline produces a table of zeroes.

    **`count(*)` is not the right count, and using it reported passing tests as
    failures.** A test's verdict is `fail_calc` applied to its result set, which
    defaults to `count(*)` but does not have to be: `dbt_utils.equal_rowcount`
    uses `sum(coalesce(diff_count, 0))` and returns a one-row *summary* whether
    it passed or failed. Counting rows scored both of this project's
    `equal_rowcount` tests as 1 failing row against a fully green
    `dbt build` (PASS=387, ERROR=0), so the page reporting pipeline health
    contradicted the build. Applying `fail_calc` is exactly what dbt does. With
    no manifest there is nothing to read it from, so it falls back to `count(*)`
    — right for the 351 of 354 tests here that use the default.
    """
    audit_tables = [
        row[0]
        for row in con.execute(
            """
            select table_name from information_schema.tables
            where table_schema = ?
            order by table_name
            """,
            [audit_schema],
        ).fetchall()
    ]
    catalogue = manifest_tests(manifest_path)
    if catalogue:
        # A table the manifest does not name is stale: dbt writes the audit
        # schema on every build, but it never *removes* a table whose test has
        # gone. Renaming a model orphans every audit table attached to it,
        # because the alias hash is over the test's arguments — renaming
        # `fct_emissions_energy` to `_v2` for the versioned model left 17
        # `dbt_utils_accepted_range_fct_e_<hash>` tables behind, which are empty
        # and so scored as passing, inflating the test count by 17 and showing
        # with no model attached.
        #
        # Keyed on the manifest being *present*, not on the match itself: with no
        # manifest nothing matches, and dropping everything would empty the table
        # instead of degrading to bare names. `pipeline_status` runs after
        # `dbt build`, so a loaded manifest is current by construction.
        audit_tables = [table for table in audit_tables if table in catalogue]

    rows = []
    for table in audit_tables:
        meta = catalogue.get(table, {})
        fail_calc = meta.get("fail_calc") or DEFAULT_FAIL_CALC
        severity = meta.get("severity") or "error"
        # `sum(...)` over an empty table is null, where `count(*)` would be 0.
        failing = scalar(con, f'select coalesce({fail_calc}, 0) from {audit_schema}."{table}"')
        rows.append(
            {
                "test_name": meta.get("test_name") or table,
                "test_type": meta.get("test_type"),
                "tested_model": meta.get("tested_model"),
                "tested_column": meta.get("tested_column"),
                "severity": severity,
                "failing_rows": int(failing),
                # A warn-severity test with failures is not a failure: dbt does
                # not fail the build on one, so calling it `fail` here would
                # report a red pipeline for something dbt let through.
                "status": ("fail" if severity == "error" else "warn") if failing else "pass",
                "audit_table": f"{audit_schema}.{table}",
            }
        )
    return pl.DataFrame(rows)
