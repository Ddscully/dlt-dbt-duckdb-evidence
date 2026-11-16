-- The retention triangle: one row per (cohort month, months since first order).
-- Ragged by construction — a cohort has rows only for months the extract could
-- have observed it in — so a chart must not read a missing cell as a zero.
--
-- Columns are named, not `select *`: the result ships to every visitor's
-- browser, so it carries what the pages read and a new mart column does not
-- join it unasked. A page that needs another column adds it here first.
select
    cohort_month,
    months_since_first_order,
    activity_month,
    active_customers,
    net_revenue_gbp,
    retention_pct,
    is_left_censored_cohort,
    is_complete_period
from marts.fct_retail_customer_cohorts
