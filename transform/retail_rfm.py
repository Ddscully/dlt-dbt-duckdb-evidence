"""Polars heavy-transform layer: RFM scoring and segmentation over the retail
customer dimension.

Recency / Frequency / Monetary is the standard way a retailer turns a
transaction log into something a marketing team can act on: score every customer
1–5 on how recently they bought, how often, and how much, then read the (R, F)
pair off a grid to get a segment name.

Polars rather than SQL for one narrow reason: cutting a column into quintiles.
SQL's primitive, `ntile`, splits equal values across buckets (see
`assign_quintiles`); the correct SQL is four quantiles and a five-branch `case`
per column, where Polars has `qcut`.

Run:  uv run python -m transform.retail_rfm
"""

from __future__ import annotations

import datetime as dt

import duckdb
import polars as pl

from modern_data_stack import db
from modern_data_stack.paths import warehouse_path

DUCKDB_PATH = warehouse_path()

# The quintile cut points — five buckets by convention.
QUINTILES = (0.2, 0.4, 0.6, 0.8)

# The (recency score, frequency score) -> segment grid, written out in full. The
# common rule-list form ("Champions: R>=4 and F>=4", "Loyal: R>=3 and F>=3")
# overlaps, so a label depends on rule order; twenty-five cells named once
# cannot. Monetary is carried as a score but kept out of the grid: R and F say
# what the relationship is doing, M what it is worth.
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

    `ntile(5)` fills equal-sized buckets, so it splits tied values wherever a
    boundary falls: 3,227 of the 5,881 customers share a frequency with someone
    it would score differently. `qcut` cuts on break points, so equal values
    score equally and bucket sizes follow the data (frequency: 1,626 / 944 /
    1,150 / 1,033 / 1,128). `allow_duplicates` leaves a bucket empty, rather than
    failing, when ties put two break points on one value.
    """
    labels = [str(i) for i in range(1, len(QUINTILES) + 2)]
    scores = (
        values.qcut(list(QUINTILES), labels=labels, allow_duplicates=True)
        # Via String: casting a Categorical straight to an integer yields its
        # physical dictionary index, not the label.
        .cast(pl.String)
        .cast(pl.Int32)
    )
    return scores if higher_is_better else (len(QUINTILES) + 2) - scores


def build_retail_rfm(customers: pl.DataFrame, as_of_date: dt.date) -> pl.DataFrame:
    """Score every customer and attach a segment.

    `as_of_date` has no default. Measured from today, every customer of a 2011
    extract is equally lapsed and recency loses its spread; it should be the
    extract's last observed day, and it ships as a column.
    """
    scored = customers.with_columns(
        pl.lit(as_of_date).alias("as_of_date"),
        (pl.lit(as_of_date) - pl.col("last_order_date")).dt.total_days().alias("recency_days"),
        pl.col("n_orders").alias("frequency"),
        # Net of returns: a customer who bought GBP 10k and returned GBP 9k is
        # not a GBP 10k customer.
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
            # The cell as text ("555"): a label, not a number. Both columns stay
            # null for the 28 customers with no revenue line (null
            # `net_revenue_gbp`); coalescing to 0 would score "nothing to measure"
            # as "worth nothing". `segment` is unaffected — the grid is R and F.
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
            # The conformed key, carried from the dimension, not recomputed.
            "country_iso3",
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
        # Polars sorts nulls first, which would open the table with the
        # unscored customers where the best belong.
        .sort(["rfm_total", "monetary_gbp"], descending=True, nulls_last=True)
    )


def run(duckdb_path: str = DUCKDB_PATH) -> int:
    """Read the customer dimension, score it, write `analytics.retail_rfm`.

    Returns the row count written (used as asset metadata by the orchestrator).
    """
    con = duckdb.connect(duckdb_path)
    try:
        customers = con.sql("select * from marts.dim_retail_customer").pl()
        # The extract's own horizon, read from the data.
        as_of_date = customers["last_order_date"].max()
        # `Series.max()` is None on an empty frame and typed as a wide union;
        # name both faults here rather than inside the recency arithmetic.
        if as_of_date is None:
            raise ValueError("marts.dim_retail_customer is empty — build the mart before scoring")
        if not isinstance(as_of_date, dt.date):
            raise TypeError(f"last_order_date holds {type(as_of_date).__name__}, expected a date")
        out = build_retail_rfm(customers, as_of_date)
        db.write_frames(con, {"retail_rfm": out}, "analytics")
        return out.height
    finally:
        con.close()


def main() -> None:
    print(f"wrote analytics.retail_rfm ({run()} rows)")


if __name__ == "__main__":
    main()
