-- The retention triangle: one row per (cohort month, months since first order).
-- Ragged by construction — a cohort has rows only for months the extract could
-- have observed it in — so a chart must not read a missing cell as a zero.
select * from marts.fct_retail_customer_cohorts
