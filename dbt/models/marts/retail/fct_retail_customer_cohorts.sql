-- Monthly acquisition cohorts and their retention.
-- Grain: one row per (cohort_month, months_since_first_order).
--
-- The retention triangle: do the customers acquired in a month come back?
--
--   * **Ragged by design.** Rows are generated per cohort only up to the last
--     month the extract (ending 2011-12) could observe, so a missing month is an
--     absence, not a zero, and no aggregate averages in months that could not
--     occur. `is_complete_period` flags the partial final month.
--   * **The first cohort is left-censored.** December 2009 is the extract's
--     first month, so its 955 "new" customers include long-standing ones;
--     `is_left_censored_cohort` lets a chart drop them.
--   * **Retention is against the cohort's own size**, not the previous month:
--     month-over-month figures compound, so a cohort that loses 20% and wins
--     some back would read as recovering while still down.
--
-- Anonymous orders cannot appear: this is retention among the 5,881 identified
-- customers (see `dim_retail_customer`).
with customers as (
    select * from {{ ref('dim_retail_customer') }}
),

lines as (
    select * from {{ ref('stg_retail_lines') }}
    where
        customer_id is not null
        and invoice_type = 'sale'
        and quantity > 0
),

-- Every (customer, month) they actually bought in.
activity as (
    select distinct
        customer_id,
        invoice_month
    from lines
),

cohort_sizes as (
    select
        cohort_month,
        count(*) as cohort_size,
        bool_or(is_left_censored_cohort) as is_left_censored_cohort
    from customers
    group by cohort_month
),

-- The last month in the data, which bounds every cohort's triangle.
bounds as (
    select max(invoice_month) as last_month from lines
),

-- 0..n once, as a table, filtered per cohort below (a cross join and a `where`
-- rather than a lateral `generate_series`). Set the var below the extract's
-- span and the oldest cohorts stop early, looking like churn;
-- `fct_retail_cohorts_are_not_truncated` in `_retail.yml` fails if so.
offsets as (
    select unnest(
        generate_series(0, {{ var('retail_max_cohort_age_months') }})
    ) as months_since_first_order
),

-- One row per cohort per observable month. Generated, not read off `activity`,
-- because a month in which a cohort bought nothing is a real zero and must
-- appear.
periods as (
    select
        s.cohort_month,
        s.cohort_size,
        s.is_left_censored_cohort,
        cast(o.months_since_first_order as integer) as months_since_first_order,
        strftime(
            cast(s.cohort_month || '-01' as date)
            + to_months(cast(o.months_since_first_order as integer)),
            '%Y-%m'
        ) as activity_month
    from cohort_sizes as s
    cross join offsets as o
    cross join bounds as b
    -- The raggedness: only months the extract could have observed.
    where o.months_since_first_order <= date_diff(
        'month',
        cast(s.cohort_month || '-01' as date),
        cast(b.last_month || '-01' as date)
    )
),

monthly_revenue as (
    select
        customer_id,
        invoice_month,
        sum(line_amount_gbp) as net_revenue_gbp
    from {{ ref('stg_retail_lines') }}
    where customer_id is not null and is_revenue_line
    group by customer_id, invoice_month
),

retained as (
    select
        c.cohort_month,
        a.invoice_month as activity_month,
        count(distinct a.customer_id) as active_customers,
        sum(l.net_revenue_gbp) as net_revenue_gbp
    from activity as a
    inner join customers as c on a.customer_id = c.customer_id
    left join monthly_revenue as l
        on a.customer_id = l.customer_id and a.invoice_month = l.invoice_month
    group by c.cohort_month, a.invoice_month
)

select
    p.cohort_month,
    p.months_since_first_order,
    p.activity_month,
    p.cohort_size,
    coalesce(r.active_customers, 0) as active_customers,
    coalesce(r.net_revenue_gbp, 0) as net_revenue_gbp,
    -- Against the cohort's own size, always. See the header.
    round(100.0 * coalesce(r.active_customers, 0) / p.cohort_size, 2) as retention_pct,
    case
        when coalesce(r.active_customers, 0) > 0
            then coalesce(r.net_revenue_gbp, 0) / r.active_customers
    end as revenue_per_active_customer_gbp,
    p.is_left_censored_cohort,
    -- False only for the extract's final month, nine days long (to 2011-12-09):
    -- a curve ending on a cliff is reading that, not a loss of loyalty.
    p.activity_month < b.last_month as is_complete_period
from periods as p
cross join bounds as b
left join retained as r
    on
        p.cohort_month = r.cohort_month
        and p.activity_month = r.activity_month
