-- One row per identified customer.
-- Grain: `customer_id`.
--
-- **The 243,007 lines with no customer are not in here.** They are GBP 2.67M of
-- real revenue from people who never signed in, so every per-customer figure
-- downstream (cohorts, RFM, average order value) covers a subset of the
-- business. `fct_retail_order_line` keeps every line, and the Evidence page
-- reports both totals. Anonymous rows are 22.8% of lines but 13.8% of revenue —
-- quote the share you mean.
--
-- 5,881 of the 5,942 customer ids reach this table; the other 61 never made a
-- purchase (cancellations or zero-priced rows only). `cohort_month` is the month
-- of the first *purchase*, not the first appearance.
--
-- `is_left_censored_cohort` marks the extract's first month (2009-12): those
-- customers may have been customers for years before the file starts.
with lines as (
    select * from {{ ref('stg_retail_lines') }}
    where customer_id is not null
),

purchases as (
    select * from lines
    where invoice_type = 'sale' and quantity > 0
),

first_purchase as (
    select
        customer_id,
        min(invoice_date) as first_order_date,
        min(invoice_month) as cohort_month
    from purchases
    group by customer_id
),

-- The first order — the one thing about a customer knowable on day one. An
-- invoice, not a day, so "order" means what `n_orders` counts. Ordered by
-- `min(invoice_ts)` (an invoice can carry several timestamps), then `invoice`
-- (a customer can open two in one minute), so the pick is stable between builds.
first_order_line as (
    select
        customer_id,
        invoice,
        row_number() over (
            partition by customer_id order by min(invoice_ts), invoice
        ) as order_seq
    from purchases
    group by customer_id, invoice
),

-- Summed over `is_revenue_line`, as `net_revenue_gbp` is, so the two compare.
-- Null, not zero, for the 47 customers whose first invoice held no product line
-- (a `Manual` adjustment or the test SKU): no revenue reading, rather than no
-- revenue. 28 have a null `net_revenue_gbp` for the same reason.
first_order_value as (
    select
        o.customer_id,
        sum(l.line_amount_gbp) filter (where l.is_revenue_line) as first_order_gbp
    from first_order_line as o
    inner join lines as l
        on o.customer_id = l.customer_id and o.invoice = l.invoice
    where o.order_seq = 1
    group by o.customer_id
),

-- The extract's first month, as a row to cross join rather than a scalar
-- subquery.
censoring as (
    select min(cohort_month) as first_cohort_month from first_purchase
),

activity as (
    select
        customer_id,
        max(invoice_date) as last_order_date,
        count(distinct invoice) filter (where invoice_type = 'sale') as n_orders,
        count(distinct invoice) filter (where invoice_type = 'cancellation') as n_cancellations,
        count(distinct invoice_month) as n_active_months,
        count(distinct stock_code) filter (where item_type = 'product') as n_distinct_products,
        sum(quantity) filter (where is_revenue_line and quantity > 0) as units_bought,
        sum(line_amount_gbp) filter (where is_revenue_line) as net_revenue_gbp,
        sum(line_amount_gbp) filter (where is_revenue_line and quantity > 0) as gross_revenue_gbp,
        sum(line_amount_gbp) filter (where is_revenue_line and quantity < 0) as returned_gbp,
        -- A customer's country is almost fixed (13 customers have two). The
        -- count ships beside the label, so a Type-1 overwrite of a moved
        -- customer's history is visible.
        count(distinct country) as n_countries,
        max(country) as country,
        -- The code belonging to the label picked above, not an independent
        -- `max`. Inside a struct because `max_by` skips null values and three
        -- seed labels map to no code (`Unspecified`, `West Indies`, `European
        -- Community`): a customer seen in `United Kingdom` and `Unspecified`
        -- would otherwise pair `Unspecified` with `GBR`. A struct with a null
        -- field is not null. Latent on current data; a unit test guards it.
        (max_by({ 'country_iso3': country_iso3 }, country)).country_iso3
            as country_iso3
    from lines
    group by customer_id
)

select
    a.customer_id,
    a.country,
    a.country_iso3,
    a.n_countries,
    a.n_countries > 1 as has_moved_country,
    f.first_order_date,
    f.cohort_month,
    a.last_order_date,
    -- Whole days between first and last purchase. Zero, not null, for the 1,626
    -- one-order customers.
    date_diff('day', f.first_order_date, a.last_order_date) as tenure_days,
    a.n_orders,
    a.n_cancellations,
    a.n_active_months,
    a.n_distinct_products,
    a.units_bought,
    a.gross_revenue_gbp,
    a.returned_gbp,
    a.net_revenue_gbp,
    v.first_order_gbp,
    case
        when a.n_orders > 0 then a.net_revenue_gbp / a.n_orders
    end as avg_order_value_gbp,
    -- By value, not order count: one item from each of ten orders is not a
    -- returned pallet.
    case
        when a.gross_revenue_gbp > 0 then -100.0 * a.returned_gbp / a.gross_revenue_gbp
    end as return_rate_pct,
    a.n_orders > 1 as is_repeat_customer,
    f.cohort_month = c.first_cohort_month as is_left_censored_cohort
from activity as a
inner join first_purchase as f on a.customer_id = f.customer_id
inner join first_order_value as v on a.customer_id = v.customer_id
cross join censoring as c
