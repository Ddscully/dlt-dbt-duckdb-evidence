-- Customer grain, for the day-one question: does what someone spends on their
-- first order tell you what they will be worth?
--
-- `retail_rfm` is also at customer grain, and this is deliberately not folded
-- into it. That table is the Polars layer's output — a scoring of behaviour to
-- date — and `first_order_gbp` is a dimension attribute that exists whether or
-- not anyone scores anything. Carrying it on the RFM table would have made the
-- Polars step a dependency of a fact that dbt already knows.
--
-- Columns are picked rather than `select *`: the dimension has 21 of them and
-- this ships as a Parquet file to every visitor's browser.
--
-- **`customer_id` was in that list until the classification work and no chart
-- ever read it** — the discipline in the paragraph above was applied to the
-- column count and not to what the columns were. It is gone now.
--
-- The four that remain are quasi-identifiers and they stay, which is a decision
-- rather than an oversight: a scatter of first-order value against lifetime
-- value *is* one mark per customer, so there is no version of this chart that
-- is not per-person data. 5,804 of the 5,881 customers (98.7%) are unique on
-- these money columns alone — a number `docs/DATA_PROTECTION.md` states rather
-- than leaves to be discovered. It is publishable because the source is already
-- public —
-- UCI redistributes the whole transaction log under CC BY 4.0 — and the day
-- that stops being true is the day this query has to become an aggregate.
select
    first_order_gbp,
    net_revenue_gbp,
    n_orders,
    is_repeat_customer,
    is_left_censored_cohort
from marts.dim_retail_customer
