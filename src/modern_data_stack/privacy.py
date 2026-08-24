"""Column classification and pseudonymisation at a publication boundary.

A warehouse can hold a column that its *published* copy must not. This module is
the mechanism for saying which columns those are and rewriting them on the way
out; which columns they are, and what the labels mean, is the project's to
answer (see `scripts/export_warehouse.py`).

## Why the boundary and not the model

The obvious place to mask a column is in the transformation that reads it — a
macro on the staging model, so nothing downstream ever sees the clear value.
That does not work here, and the reason is worth keeping:

* **The published database ships `raw` as well as the modelled layers**, so a
  mask applied in staging leaves the clear value one schema away in the same
  file. Whoever downloads it has the original.
* **The staging models are views**, and a view in the published copy recomputes
  from `raw` when it is queried. Mask the raw column *as well* and the view
  hashes an already-hashed value, so the shipped views disagree with the shipped
  marts about who customer `a3f1…` is — a corruption that no build step and no
  row count would show.

Applying the policy once, to the finished copy, avoids both: base tables are
rewritten, views recompute from the rewritten tables, and the two agree because
the value was hashed exactly once.

## What it does and does not buy

Pseudonymisation is not anonymisation, and the arithmetic is unforgiving. A
hash over a low-cardinality identifier drawn from a guessable domain — five-digit
customer numbers, say — is reversed by hashing the domain, which is a few
thousand operations. The salt is the whole of the protection, which is why it is
required rather than defaulted: `salt` is a secret, and a caller that has not got
one gets an exception instead of a plausible-looking hex column.

It also does nothing at all about the columns *beside* the identifier. Whether a
person can be picked out of the published rows is a property of the whole row,
which is what `k_anonymity` measures.
"""

from __future__ import annotations

from collections.abc import Iterable

import duckdb

from .db import row, scalar

# 64 bits of the digest, hex-encoded. Long enough that a collision over any
# plausible number of subjects is not a thing that happens (5,881 values give a
# birthday probability around 1e-12), short enough that it is not 64 characters
# on every row of a million-row fact.
PSEUDONYM_LENGTH = 16

# What a pseudonymised value looks like, and the whole basis of `verify`. It is
# decisive rather than heuristic *because* the identifiers this replaces are not
# hex: a five-digit customer number cannot match it, so a column that failed to
# be rewritten cannot pass.
PSEUDONYM_PATTERN = f"^[0-9a-f]{{{PSEUDONYM_LENGTH}}}$"

# One classified column, as (schema, table, column).
Column = tuple[str, str, str]


class PolicyError(RuntimeError):
    """A classified column reached the boundary in the clear, or would have."""


def pseudonym_expr(column: str, *, length: int = PSEUDONYM_LENGTH) -> str:
    """SQL rewriting `column` to a salted pseudonym. The salt is a parameter.

    **`||`, never `concat()`.** DuckDB's `concat` ignores NULL arguments, so
    `concat(customer_id, $salt)` on a row with no customer hashes the bare salt —
    every anonymous row collapses onto one fabricated subject that looks exactly
    like a real one. `||` propagates the NULL, which is the truth: a row with no
    identifier has no pseudonym either.
    """
    return f"substr(sha256({column} || $salt), 1, {length})"


def classifications(manifest: dict, *, key: str = "pii") -> dict[Column, str]:
    """Every classified column dbt knows about, as `{(schema, table, column): label}`.

    Read from a compiled `manifest.json` rather than from the ymls, because the
    manifest is where a model's *relation* is resolved — a project overriding
    `generate_schema_name` (as this one does) has ymls that never mention the
    schema a model actually lands in.

    Sources are included: the landing tables are where an identifier enters, and
    a classification that starts at staging has already missed it.

    The label vocabulary belongs to the caller. Nothing here reads it except
    `classified_columns`, which filters on it.
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

    Defence in depth, and the failure it exists for is the ordinary one: a model
    added later that carries `customer_id` and whose author never touched the
    classification. Declaring the column is still the contract — this only makes
    forgetting it fail closed rather than ship.
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
        # Refuse a second pass. Hashing a pseudonym yields another 16 hex
        # characters, so `verify` cannot tell one application from two after the
        # fact — the check has to happen before the write. Running this against a
        # warehouse rather than a copy is the same mistake wearing a different
        # hat, and it is unrecoverable: the clear values are gone.
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

    Checks views as well as tables, because a view is how an unrewritten `raw`
    column reaches a consumer without appearing to.
    """
    # An empty set verifies clean, and that is correct *here*: a database simply
    # holding none of the classified columns is a no-op, not a failure. What is
    # not correct is an empty set arriving because nothing was ever classified —
    # that belongs to whoever decides the policy, and `scripts/export_warehouse.py`
    # refuses it there rather than letting this function decide for every caller.
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
