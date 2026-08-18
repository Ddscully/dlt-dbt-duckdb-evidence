"""Polars heavy-transform layer: RFM scoring and segmentation over the retail
customer dimension.

Recency / Frequency / Monetary is the standard way a retailer turns a
transaction log into something a marketing team can act on: score every customer
1–5 on how recently they bought, how often, and how much, then read the (R, F)
pair off a grid to get a segment name.

**This is the first transform here that Polars is genuinely better at than SQL**,
and the reason is narrow enough to state exactly. The operation is "cut a column
into quintiles"; SQL's primitive for that is `ntile`, and `ntile` is *wrong* for
this. See `assign_quintiles` below — it splits equal values across buckets, and
in this data that hits 3,227 of 5,881 customers. Doing it correctly in SQL means
computing four quantiles per column and hand-writing a five-branch `case` over
them, three times over. Polars has the correct operation as a primitive.

Run:  uv run python -m transform.retail_rfm
"""

from __future__ import annotations

import datetime as dt

import duckdb
import polars as pl

from modern_data_stack.paths import warehouse_path

DUCKDB_PATH = warehouse_path()

# The quintile cut points. Five buckets is convention, not arithmetic — it is
# small enough to name each cell of the 5x5 grid below and large enough that the
# top bucket is a shortlist rather than a fifth of the file.
QUINTILES = (0.2, 0.4, 0.6, 0.8)

# The (recency score, frequency score) -> segment grid, written out in full.
#
# The widely-copied version of this is a list of rules ("Champions: R>=4 and
# F>=4", "Loyal: R>=3 and F>=3", ...) whose conditions **overlap**, so which
# label a customer gets depends on the order the rules are evaluated in — a
# customer at R=4, F=4 matches both of those. That is invisible in review and
# survives a refactor that reorders the branches. Twenty-five cells, each named
# once, cannot be ambiguous, and the whole policy is legible as a table.
#
# Monetary is deliberately *not* in the grid. It is carried as a score, because
# it answers a different question: R and F say what the relationship is doing,
# M says what it is worth. Folding M in would make "a lapsed big spender" and "a
# frequent small one" compete for the same cell, and the two need different
# interventions.
SEGMENT_GRID: dict[tuple[int, int], str] = {
    # R=5 — bought most recently
    (5, 1): "New Customers",
    (5, 2): "Promising",
    (5, 3): "Potential Loyalist",
    (5, 4): "Loyal Customers",
    (5, 5): "Champions",
    (4, 1): "Promising",
    (4, 2): "Potential Loyalist",
    (4, 3): "Potential Loyalist",
    (4, 4): "Loyal Customers",
    (4, 5): "Champions",
    (3, 1): "About to Sleep",
    (3, 2): "About to Sleep",
    (3, 3): "Need Attention",
    (3, 4): "Loyal Customers",
    (3, 5): "Loyal Customers",
    (2, 1): "Hibernating",
    (2, 2): "Hibernating",
    (2, 3): "At Risk",
    (2, 4): "At Risk",
    (2, 5): "Can't Lose Them",
    # R=1 — longest since the last order
    (1, 1): "Lost",
    (1, 2): "Lost",
    (1, 3): "At Risk",
    (1, 4): "Can't Lose Them",
    (1, 5): "Can't Lose Them",
}


def segment_frame() -> pl.DataFrame:
    """The grid as a frame, for joining. Policy as data, not as control flow."""
    return pl.DataFrame(
        {
            "recency_score": [r for r, _ in SEGMENT_GRID],
            "frequency_score": [f for _, f in SEGMENT_GRID],
            "segment": list(SEGMENT_GRID.values()),
        },
        schema={"recency_score": pl.Int32, "frequency_score": pl.Int32, "segment": pl.String},
    )


def assign_quintiles(values: pl.Series, *, higher_is_better: bool = True) -> pl.Series:
    """Score a column 1–5 by **value**, not by rank position.

    This is the whole argument for doing the scoring here. The obvious SQL is
    `ntile(5) over (order by n_orders)`, which fills five buckets of equal
    *size* — so when values tie, it splits the tie wherever the boundary happens
    to fall. Frequency is a small integer with enormous ties, and the damage is
    measurable: 1,626 customers have placed exactly one order and `ntile` puts
    some of them in quintile 1 and the rest in quintile 2. Across the four tied
    values that straddle a boundary (1, 2, 4 and 8 orders) that is **3,227 of
    5,881 customers — 54.9%** who could be scored differently from someone whose
    behaviour is identical to theirs.

    That is not a rounding detail. The score is supposed to be a property of the
    customer; under `ntile` it is a property of where they landed in a sort, and
    two identical customers can end up in different marketing campaigns.

    `qcut` cuts on the *break points* instead, so equal values always score
    equally. The price is that the buckets are no longer equal in size — here
    they run 1,626 / 944 / 1,150 / 1,033 / 1,128 — and that is the correct trade:
    the unevenness is a fact about the customer base, not an artefact.

    `allow_duplicates` covers the case where ties are heavy enough that two
    quantiles land on the same value; the bucket between them is then empty
    rather than the call failing.
    """
    labels = [str(i) for i in range(1, len(QUINTILES) + 2)]
    scores = (
        values.qcut(list(QUINTILES), labels=labels, allow_duplicates=True)
        # Casting a Categorical straight to an integer yields the *physical*
        # dictionary index — the order the labels were first seen, which for a
        # sorted-by-quantile column is not the label order. Via String is the
        # only reading that means what it says.
        .cast(pl.String)
        .cast(pl.Int32)
    )
    return scores if higher_is_better else (len(QUINTILES) + 2) - scores


def build_retail_rfm(customers: pl.DataFrame, as_of_date: dt.date) -> pl.DataFrame:
    """Score every customer and attach a segment.

    `as_of_date` is a parameter and has no default, which is the second thing
    worth knowing about this model. Recency is days-since-last-order, and the
    natural expression for that is `today - last_order_date` — which, against a
    2011 extract, makes every customer in the file equally and enormously
    lapsed. Recency then has almost no spread left to cut into quintiles, and
    the segmentation quietly becomes a frequency ranking with a recency column
    stapled to it. So recency is measured against the **last day the extract
    observed**, and that date ships as a column so nothing downstream has to
    guess which clock the scores are on.
    """
    scored = customers.with_columns(
        pl.lit(as_of_date).alias("as_of_date"),
        (pl.lit(as_of_date) - pl.col("last_order_date")).dt.total_days().alias("recency_days"),
        pl.col("n_orders").alias("frequency"),
        # Net of returns. A customer who bought GBP 10k and sent GBP 9k of it
        # back is not a GBP 10k customer, and gross revenue says they are — the
        # seven customers whose net is negative are exactly the ones a gross
        # measure would flatter most.
        pl.col("net_revenue_gbp").alias("monetary_gbp"),
    )

    scored = scored.with_columns(
        assign_quintiles(scored["recency_days"], higher_is_better=False).alias("recency_score"),
        assign_quintiles(scored["frequency"]).alias("frequency_score"),
        assign_quintiles(scored["monetary_gbp"]).alias("monetary_score"),
    )

    return (
        scored.join(segment_frame(), on=["recency_score", "frequency_score"], how="left")
        .with_columns(
            # The concatenated cell, e.g. "555". Kept as text on purpose: it is
            # a label, and 155 is not eleven times 55.
            #
            # **Null for the 28 customers with no revenue line**, and left that
            # way. `monetary_gbp` is `net_revenue_gbp`, which `dim_retail_customer`
            # already publishes as null for the customers whose orders held only a
            # `Manual` adjustment or postage — so `qcut` returns null, and both of
            # these propagate it. Coalescing to 0 would score them in the bottom
            # monetary quintile, which reads as "we measured them and they are
            # worth nothing" rather than "there is nothing to measure", and it is
            # the model's existing convention that the second is a null. `segment`
            # is unaffected: the grid is R and F only, so all 5,881 are segmented.
            pl.concat_str(
                pl.col("recency_score"), pl.col("frequency_score"), pl.col("monetary_score")
            ).alias("rfm_cell"),
            (pl.col("recency_score") + pl.col("frequency_score") + pl.col("monetary_score")).alias(
                "rfm_total"
            ),
        )
        .select(
            "customer_id",
            "country",
            "as_of_date",
            "cohort_month",
            "first_order_date",
            "last_order_date",
            "recency_days",
            "frequency",
            "monetary_gbp",
            "recency_score",
            "frequency_score",
            "monetary_score",
            "rfm_cell",
            "rfm_total",
            "segment",
            "avg_order_value_gbp",
            "n_distinct_products",
            "return_rate_pct",
            "is_left_censored_cohort",
        )
        # `nulls_last` is not a default worth taking here. Polars sorts nulls
        # *first*, so descending by `rfm_total` opened the table with the 28
        # customers who have no monetary score at all — the least informative
        # rows in the file sitting where the best customers belong, on a table
        # whose whole purpose is "read the top of it".
        .sort(["rfm_total", "monetary_gbp"], descending=True, nulls_last=True)
    )


def run(duckdb_path: str = DUCKDB_PATH) -> int:
    """Read the customer dimension, score it, write `analytics.retail_rfm`.

    Returns the row count written (used as asset metadata by the orchestrator).
    """
    con = duckdb.connect(duckdb_path)
    try:
        customers = con.sql("select * from marts.dim_retail_customer").pl()
        # The extract's own horizon, read from the data rather than assumed, so
        # a re-run against a longer extract re-bases the scores automatically.
        as_of_date = customers["last_order_date"].max()
        out = build_retail_rfm(customers, as_of_date)
        con.sql("create schema if not exists analytics")
        con.register("out_df", out)  # DuckDB reads Polars frames directly
        con.sql("create or replace table analytics.retail_rfm as select * from out_df")
        return out.height
    finally:
        con.close()


def main() -> None:
    print(f"wrote analytics.retail_rfm ({run()} rows)")


if __name__ == "__main__":
    main()
