-- Monthly acquisition cohorts and their retention.
-- Grain: one row per (cohort_month, months_since_first_order).
--
-- The classic retention triangle, and the first thing in this warehouse that
-- measures *behaviour over time by group* rather than a level. It exists because
-- it is unreachable at country-year grain: you cannot ask "do the customers we
-- won in March come back" of a national statistic.
--
-- Three things the shape of this table is deliberately honest about:
--
--   * **The triangle is ragged, and only the rows that could have happened are
--     here.** A cohort born in November 2011 has no month-12 row, because the
--     extract ends in December 2011 — it is not a zero, it is an absence. Rows
--     are generated per cohort only up to the last month observable for that
--     cohort, so a `sum` over the table never quietly averages in months that
--     had no chance to occur. `is_complete_period` says so per row anyway, for
--     anything that slices differently.
--   * **The first cohort is left-censored and marked.** December 2009 is the
--     extract's first month, so its 955 "new" customers include everyone who had
--     been buying for years already. Their month-0 is a fiction of when the file
--     starts. `is_left_censored_cohort` carries through from
--     `dim_retail_customer` so a chart can drop them in a `where` rather than in
--     a footnote nobody reads.
--   * **Retention is measured against the cohort's own size, not against the
--     previous month.** Month-over-month "retention" compounds, so a cohort that
--     loses 20% then wins some back reads as recovering when it is still down;
--     against the base it reads as still down. The base is the version that
--     answers "of the customers we acquired, how many are still here".
--
-- Anonymous orders cannot appear here at all — see `dim_retail_customer`. This
-- is retention among the 5,881 identified customers, which is a subset of the
-- business and never the whole of it.
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

-- 0..n, once, as a table, then filtered per cohort below. The alternative is a
-- lateral `generate_series` correlated on each cohort's own span, which says the
-- same thing less legibly; this is a cross join and a `where`.
--
-- The bound is a var and not a literal because it is the one number here that
-- can silently *truncate* the answer: set it below the extract's span and the
-- oldest cohorts simply stop early, with no error and a retention curve that
-- looks like churn. `fct_retail_cohorts_are_not_truncated` in `_retail.yml` is
-- what makes that loud — it asserts the first cohort reaches the last month.
offsets as (
    select unnest(
        generate_series(0, {{ var('retail_max_cohort_age_months') }})
    ) as months_since_first_order
),

-- One row per cohort per month that *could* have been observed. Generated
-- rather than read off the activity table, because a month in which a cohort
-- bought nothing is a real zero and must appear — taking the triangle from
-- activity alone would silently drop exactly the months that matter.
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
    -- The raggedness, in one line: a cohort gets rows only up to the last month
    -- the extract could have observed it in.
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
    -- False only for the final month of the extract, which is nine days long —
    -- the file stops on 2011-12-09. Every cohort's last row is therefore a
    -- partial month, and a retention curve that ends on a cliff is reading that
    -- and not a collapse in loyalty.
    p.activity_month < b.last_month as is_complete_period
from periods as p
cross join bounds as b
left join retained as r
    on
        p.cohort_month = r.cohort_month
        and p.activity_month = r.activity_month
