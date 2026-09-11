"""Column classification and pseudonymisation at a publication boundary.

A warehouse can hold a column that its *published* copy must not. This module is
the mechanism for saying which columns those are and rewriting them on the way
out; which columns they are, and what the labels mean, is the project's to
answer (see `publish/export_warehouse.py`).

## Why the boundary and not the model

A mask in a model would change the working warehouse too, and would miss every
relation no model declares — test-failure audit tables, a loader's scratch
copies — that a published file can still carry. Applied once to the finished
copy, and expanded by column name, the policy covers what the file actually
holds; a view over a rewritten table reads the pseudonym, and `verify` checks
views and tables alike. Applying it twice would hash a hash, so
`apply_pseudonymisation` refuses a column that already holds pseudonyms.

## What it does and does not buy

Pseudonymisation is not anonymisation. A digest of an identifier from a
guessable domain (five-digit customer numbers) is reversed by hashing every
candidate, so the salt is the whole of the protection and is required.

Nor does it touch the columns *beside* the identifier: whether a person can be
picked out is a property of the whole row, which `k_anonymity` measures.
"""

from __future__ import annotations

from collections.abc import Iterable

import duckdb

from .db import row, scalar

# 64 bits of the digest, hex-encoded: collision odds around 1e-12 for 5,881
# subjects, without 64 characters on every row of a million-row fact.
PSEUDONYM_LENGTH = 16

# The basis of `verify`. Decisive because the identifiers it replaces are not
# hex: a five-digit customer number cannot match it.
PSEUDONYM_PATTERN = f"^[0-9a-f]{{{PSEUDONYM_LENGTH}}}$"

# One classified column, as (schema, table, column).
Column = tuple[str, str, str]


class PolicyError(RuntimeError):
    """A classified column reached the boundary in the clear, or would have."""


def pseudonym_expr(column: str, *, length: int = PSEUDONYM_LENGTH) -> str:
    """SQL rewriting `column` to a salted pseudonym. The salt is a parameter.

    `||`, never `concat()`: `concat` ignores NULLs, so every row with no
    identifier would hash the bare salt and share one pseudonym indistinguishable
    from a real subject. `||` keeps them NULL.
    """
    return f"substr(sha256({column} || $salt), 1, {length})"


def classifications(manifest: dict, *, key: str = "pii") -> dict[Column, str]:
    """Every classified column dbt knows about, as `{(schema, table, column): label}`.

    From the compiled manifest rather than the ymls, because only the manifest
    resolves the schema a model lands in (`generate_schema_name` may override
    it). Sources are included: that is where an identifier enters. The label
    vocabulary is the caller's.
    """
    found: dict[Column, str] = {}
    nodes = list(manifest.get("nodes", {}).values())
    for node in nodes:
        relation = (node["schema"], node.get("alias") or node["name"])
        for column, spec in (node.get("columns") or {}).items():
            label = (spec.get("meta") or {}).get(key)
            if label is not None:
                found[(*relation, column)] = label
    for source in manifest.get("sources", {}).values():
        relation = (source["schema"], source.get("identifier") or source["name"])
        for column, spec in (source.get("columns") or {}).items():
            label = (spec.get("meta") or {}).get(key)
            if label is not None:
                found[(*relation, column)] = label
    return found


def classified_columns(manifest: dict, labels: Iterable[str], *, key: str = "pii") -> list[Column]:
    """The columns carrying one of `labels`, sorted."""
    wanted = set(labels)
    return sorted(c for c, label in classifications(manifest, key=key).items() if label in wanted)


def _relations(con: duckdb.DuckDBPyConnection) -> dict[tuple[str, str], str]:
    rows = con.execute(
        """
        select table_schema, table_name, table_type
        from information_schema.tables
        """
    ).fetchall()
    return {(schema, table): kind for schema, table, kind in rows}


def _columns(con: duckdb.DuckDBPyConnection) -> list[Column]:
    return [
        (schema, table, column)
        for schema, table, column in con.execute(
            """
            select table_schema, table_name, column_name
            from information_schema.columns
            order by table_schema, table_name, ordinal_position
            """
        ).fetchall()
    ]


def expand_by_name(con: duckdb.DuckDBPyConnection, declared: Iterable[Column]) -> list[Column]:
    """Every column in the database sharing a *name* with a declared one.

    Defence in depth: a later model carrying `customer_id` whose author never
    classified it is still rewritten. Declaring the column remains the contract.
    """
    names = {column for _, _, column in declared}
    return sorted((s, t, c) for s, t, c in _columns(con) if c in names)


def apply_pseudonymisation(
    con: duckdb.DuckDBPyConnection, columns: Iterable[Column], salt: str
) -> list[Column]:
    """Rewrite each column in place. Returns the base tables actually touched.

    Views are skipped rather than refused: a view over a rewritten table already
    reads the pseudonym, and `verify` is what proves it.
    """
    if not salt:
        raise PolicyError("a salt is required — an unsalted digest is not a pseudonym")

    kinds = _relations(con)
    touched: list[Column] = []
    for schema, table, column in sorted(set(columns)):
        if kinds.get((schema, table)) != "BASE TABLE":
            continue
        quoted = f'"{column}"'
        # Refuse a second pass, before writing: a hashed pseudonym is still 16 hex
        # characters, so `verify` cannot tell one application from two. (Run
        # against the warehouse instead of a copy, the clear values are gone.)
        already = scalar(
            con,
            f'select count(*) from "{schema}"."{table}" where {quoted} is not null'
            f"   and regexp_matches({quoted}, '{PSEUDONYM_PATTERN}')",
        )
        if already:
            raise PolicyError(
                f"{schema}.{table}.{column} already holds {already:,} pseudonymised values — "
                "refusing to hash them again. Apply the policy to a fresh copy of the "
                "warehouse, not to one it has already been applied to."
            )
        con.execute(
            f'update "{schema}"."{table}" set {quoted} = {pseudonym_expr(quoted)}'
            f" where {quoted} is not null",
            {"salt": salt},
        )
        touched.append((schema, table, column))
    return touched


def verify(con: duckdb.DuckDBPyConnection, columns: Iterable[Column]) -> None:
    """Raise unless every value of every named column is a pseudonym or NULL.

    Checks views as well as tables: a view over an unrewritten table exposes it
    without appearing to.
    """
    # An empty set verifies clean. Refusing a policy that classified nothing is
    # the caller's decision (`publish/export_warehouse.py` makes it).
    offenders = []
    for schema, table, column in sorted(set(columns)):
        bad = scalar(
            con,
            f'select count(*) from "{schema}"."{table}" '
            f'where "{column}" is not null '
            f"  and not regexp_matches(\"{column}\", '{PSEUDONYM_PATTERN}')",
        )
        if bad:
            offenders.append(f"{schema}.{table}.{column} ({bad:,} rows)")
    if offenders:
        raise PolicyError("classified columns are not pseudonymised: " + ", ".join(offenders))


def k_anonymity(
    con: duckdb.DuckDBPyConnection, relation: str, columns: Iterable[str]
) -> dict[str, int]:
    """How many rows of `relation` are alone in their combination of `columns`.

    The question a masked identifier does not answer: with the id gone, can a row
    still be picked out? `singletons` is the count that can — the rows sitting in
    a group of one — and `rows` is what it is out of.
    """
    quasi = ", ".join(f'"{c}"' for c in columns)
    singletons, rows, largest = row(
        con,
        f"""
        with g as (select {quasi}, count(*) as k from {relation} group by all)
        select
            coalesce(sum(case when k = 1 then 1 else 0 end), 0),
            coalesce(sum(k), 0),
            coalesce(max(k), 0)
        from g
        """,
    )
    return {"singletons": singletons, "rows": rows, "largest_group": largest}
