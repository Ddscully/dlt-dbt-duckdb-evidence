"""How identifiable are the people in the published customer-grain tables?

Prints the table in `docs/DATA_PROTECTION.md`, from the warehouse rather than
from memory, so the numbers there can be re-checked when the models move.

Run:  uv run python -m scripts.measure_disclosure_risk      (or `just disclosure-risk`)

The question is not whether the identifier is masked — the export handles that,
and `tests/test_privacy.py` proves it. It is whether a person can still be picked
out once it is gone, which is a property of the *other* columns and is why
`quasi_identifier` is a label with no action attached. Each row below counts the
customers who are alone in their combination of values: alone means findable by
anyone holding those few facts about them.

Nothing here writes anything. It reads the warehouse read-only and prints.
"""

from __future__ import annotations

import argparse

import duckdb

from modern_data_stack import privacy
from modern_data_stack.paths import warehouse_path

DUCKDB_PATH = warehouse_path()

# Each entry is (relation, columns, why this combination is worth measuring).
# They are not arbitrary: every one is either a set someone could plausibly know
# about a person, or the exact column list of something this project publishes.
COMBINATIONS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("marts.dim_retail_customer", ("country",), "country alone"),
    ("marts.dim_retail_customer", ("country", "cohort_month"), "and the month they arrived"),
    ("marts.dim_retail_customer", ("country", "first_order_date"), "and the day they arrived"),
    (
        "marts.dim_retail_customer",
        ("country", "first_order_date", "first_order_gbp"),
        "and what they spent on it",
    ),
    (
        "marts.dim_retail_customer",
        ("first_order_gbp", "net_revenue_gbp", "n_orders"),
        "the site's customer extract, id removed",
    ),
    ("marts.dim_retail_customer", ("net_revenue_gbp",), "lifetime value alone"),
    (
        "analytics.retail_rfm",
        ("segment", "monetary_gbp", "recency_days", "frequency"),
        "the site's RFM extract as it ships now",
    ),
    (
        "analytics.retail_rfm",
        ("segment", "recency_days", "frequency"),
        "the same without the money column",
    ),
)


def run(duckdb_path: str = DUCKDB_PATH) -> list[dict]:
    con = duckdb.connect(duckdb_path, read_only=True)
    try:
        return [
            {
                "relation": relation,
                "columns": columns,
                "note": note,
                **privacy.k_anonymity(con, relation, columns),
            }
            for relation, columns, note in COMBINATIONS
        ]
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--warehouse", default=DUCKDB_PATH, help="source DuckDB file")
    args = parser.parse_args()

    print(f"{'columns':62s} {'alone':>7s} {'of':>7s} {'':>7s}  largest")
    for row in run(args.warehouse):
        share = 100.0 * row["singletons"] / row["rows"] if row["rows"] else 0.0
        columns = ", ".join(row["columns"])
        print(
            f"{columns[:62]:62s} {row['singletons']:7,d} {row['rows']:7,d} "
            f"{share:6.1f}%  {row['largest_group']:,}"
        )
        print(f"  {row['relation']} — {row['note']}")


if __name__ == "__main__":
    main()
