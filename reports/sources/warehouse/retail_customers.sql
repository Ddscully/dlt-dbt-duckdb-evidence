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
select
    customer_id,
    first_order_gbp,
    net_revenue_gbp,
    n_orders,
    is_repeat_customer,
    is_left_censored_cohort
from marts.dim_retail_customer
