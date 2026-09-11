-- Customer grain, for the day-one question: does what someone spends on their
-- first order tell you what they will be worth?
--
-- Read from the dimension rather than `retail_rfm`: `first_order_gbp` is a
-- dimension attribute, and the Polars scoring should not be its dependency.
--
-- Columns are picked, not `select *`: this ships to every visitor's browser,
-- and nothing identifying is drawn. What remains is still per-person data — a
-- scatter of first-order against lifetime value is one mark per customer, and
-- nearly every customer is unique on the money columns (`docs/DATA_PROTECTION.md`
-- has the measurement). It is publishable because UCI already publishes the
-- whole log under CC BY 4.0; if that changes, this must become an aggregate.
select
    first_order_gbp,
    net_revenue_gbp,
    is_repeat_customer,
    is_left_censored_cohort
from marts.dim_retail_customer
