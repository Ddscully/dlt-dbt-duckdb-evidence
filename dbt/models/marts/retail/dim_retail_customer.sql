-- One row per identified customer.
-- Grain: `customer_id`.
--
-- **The 243,007 lines with no customer are not in here, and that is the single
-- most important thing about this table.** They are not errors and they are not
-- droppable: they are GBP 2.67M of real revenue from people who never signed in.
-- Every per-customer figure downstream — cohort retention, RFM, average order
-- value — is therefore computed over a *subset* of the business, and the honest
-- way to say so is to make the subset explicit here rather than to let a
-- `where customer_id is not null` disappear into a join. `fct_retail_order_line`
-- keeps every line; this dimension covers the ones a customer can be attached
-- to, and the Evidence page reports both totals side by side.
--
-- The two shares differ and the gap is the point: anonymous rows are **22.8% of
-- lines but 13.8% of revenue**, because an order nobody signed in for is a
-- smaller order. Quoting the line share as if it were the revenue share
-- overstates the hole by nine points.
--
-- **5,881 of the 5,942 customer ids reach this table.** The 61 that don't never
-- made a purchase — they appear only on cancellations or zero-priced rows — and
-- a customer dimension built by `select distinct customer_id` would have carried
-- them with a null cohort and a zero tenure forever. `cohort_month` is
-- likewise the month of the first *purchase*, not of the first appearance: 140
-- customers have an earlier non-purchase row, and dating them from it would put
-- them in a cohort they bought nothing in.
--
-- The left-censoring caveat is real and is carried as a column. The extract
-- starts on 2009-12-01, so every customer whose first order falls in that month
-- may well have been a customer for years already — their "cohort" is an
-- artefact of when the file starts. `is_left_censored_cohort` marks them so a
-- retention chart can drop or annotate them instead of reporting the extract's
-- start date as a customer's tenure.
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

-- What the customer arrived with. `first_order_date` says when they turned up;
-- this says how much they spent doing it, and it is the only thing about a
-- customer that is knowable on day one — everything else in this table needs a
-- relationship to have happened first.
--
-- **An invoice, not a day.** `n_orders` counts invoices, so "order" has to mean
-- the same thing in both columns or they quietly disagree, and 393 of the 5,881
-- customers bought twice on the day they arrived. Ordering on `min(invoice_ts)`
-- because 83 invoices in the file carry more than one timestamp, then on
-- `invoice` because 11 customers opened two at the same minute — a tie-break
-- that isn't deterministic is a column that changes between builds for no
-- change in the data.
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

-- Summed over `is_revenue_line` exactly as `net_revenue_gbp` is, so the two are
-- the same measurement over different windows and the ratio between them means
-- something. That also makes it **null, not zero, for the 47 customers whose
-- first invoice carried no product line at all** — a `Manual` adjustment or the
-- test SKU. Zero would say they bought nothing; null says this order has no
-- revenue reading, which is what happened. 28 customers already have a null
-- `net_revenue_gbp` for the same reason.
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

-- The extract's first month, as a row rather than as a scalar subquery in the
-- select. Same value, but it joins instead of correlating — which is the house
-- style and, here, also the difference between one pass and one per row.
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
        -- The country a customer transacts from is *almost* fixed: 13 of 5,942
        -- move. Both are carried — one as the dimension's answer, the
        -- count as the reason not to trust it blindly — because a Type-1
        -- overwrite that silently relabels a customer's whole history is the
        -- classic dimension bug, and 13 rows is exactly the size at which
        -- nobody notices.
        count(distinct country) as n_countries,
        max(country) as country,
        -- `max_by`, not `max`: the code has to be the one belonging to the
        -- label the line above picked. For the 13 customers who transact from
        -- two countries a plain `max` on each column independently can name
        -- one country and code another, which is a row that agrees with
        -- nothing and reads as a mapping bug.
        --
        -- **The struct is load-bearing, because `max_by` skips nulls.**
        -- DuckDB's `arg_max` ignores rows whose *value* argument is null, and
        -- three seed labels map to no code on purpose (`Unspecified`,
        -- `West Indies`, `European Community`, all `not_a_country`). So a
        -- customer transacting from both `United Kingdom` and `Unspecified`
        -- took the label `Unspecified` and the code `GBR` — exactly the
        -- mismatch the paragraph above says this line prevents. A struct
        -- holding a null field is not itself null, so the row survives the
        -- aggregate and the pairing holds. Latent on current data: all 13
        -- multi-country customers use mapped labels, which is why the guard is
        -- a unit test rather than a data test.
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
    -- Tenure in whole days between first and last purchase. Zero for the 1,626
    -- customers who ordered once — which is a fact about the business, not a
    -- missing value, so it is not nulled.
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
    -- Return rate by value, not by order count: a customer who returns one item
    -- from each of ten orders is not the same risk as one who sends back a
    -- pallet, and the count treats them identically.
    case
        when a.gross_revenue_gbp > 0 then -100.0 * a.returned_gbp / a.gross_revenue_gbp
    end as return_rate_pct,
    a.n_orders > 1 as is_repeat_customer,
    f.cohort_month = c.first_cohort_month as is_left_censored_cohort
from activity as a
inner join first_purchase as f on a.customer_id = f.customer_id
inner join first_order_value as v on a.customer_id = v.customer_id
cross join censoring as c
