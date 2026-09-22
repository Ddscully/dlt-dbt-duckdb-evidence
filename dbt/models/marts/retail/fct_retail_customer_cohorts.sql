-- Monthly acquisition cohorts and their retention, ragged by design. The grain
-- and the caveats are the description in _retail.yml.
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

-- 0..n once, filtered per cohort below. A var below the extract's span
-- truncates the oldest cohorts; `fct_retail_cohorts_are_not_truncated` fails.
offsets as (
    select unnest(
        generate_series(0, {{ var('retail_max_cohort_age_months') }})
    ) as months_since_first_order
),

-- Generated, not read off `activity`: a month a cohort bought nothing in is a
-- real zero and must appear.
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
    -- Against the cohort's own size, never the previous month.
    round(100.0 * coalesce(r.active_customers, 0) / p.cohort_size, 2) as retention_pct,
    case
        when coalesce(r.active_customers, 0) > 0
            then coalesce(r.net_revenue_gbp, 0) / r.active_customers
    end as revenue_per_active_customer_gbp,
    p.is_left_censored_cohort,
    -- The extract's final month is nine days long (see the column description).
    p.activity_month < b.last_month as is_complete_period
from periods as p
cross join bounds as b
left join retained as r
    on
        p.cohort_month = r.cohort_month
        and p.activity_month = r.activity_month
