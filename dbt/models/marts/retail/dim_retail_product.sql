-- One row per stock code the retailer ever transacted.
-- Grain: `stock_code`.
--
-- 1,192 of the 5,131 codes carry more than one description (one carries nine):
-- variant spellings, corrections, and notes such as "wrong barcode". The label
-- is the description used on the most lines, ties broken alphabetically —
-- deterministic between builds, and preferring what the business used over the
-- latest text, which is often a one-off note.
--
-- `item_type` is constant per code by construction, hence `any_value`;
-- `_retail.yml` tests that it stays so.
with lines as (
    select * from {{ ref('stg_retail_lines') }}
),

labels as (
    select
        stock_code,
        description,
        count(*) as n_lines
    from lines
    where description is not null
    group by stock_code, description
    qualify row_number() over (
        partition by stock_code order by n_lines desc, description asc
    ) = 1
),

activity as (
    select
        stock_code,
        any_value(item_type) as item_type,
        count(*) as n_lines,
        count(distinct invoice) as n_invoices,
        count(distinct customer_id) as n_customers,
        min(invoice_date) as first_sold_date,
        max(invoice_date) as last_sold_date,
        sum(quantity) filter (where invoice_type = 'sale' and quantity > 0) as units_sold,
        -- Positive, like `fct_retail_returns.quantity_returned`, so
        -- `units_returned / units_sold` is a positive ratio.
        -sum(quantity) filter (where invoice_type = 'cancellation') as units_returned,
        sum(line_amount_gbp) filter (where is_revenue_line) as net_revenue_gbp,
        -- Median: a SKU sold at both a wholesale and a retail price has a mean
        -- that matches neither.
        median(unit_price) filter (where unit_price > 0) as median_unit_price_gbp,
        min(unit_price) filter (where unit_price > 0) as min_unit_price_gbp,
        max(unit_price) as max_unit_price_gbp,
        count(distinct description) as n_descriptions
    from lines
    group by stock_code
)

select
    a.stock_code,
    l.description,
    a.item_type,
    a.n_lines,
    a.n_invoices,
    a.n_customers,
    a.first_sold_date,
    a.last_sold_date,
    a.units_sold,
    a.units_returned,
    a.net_revenue_gbp,
    a.median_unit_price_gbp,
    a.min_unit_price_gbp,
    a.max_unit_price_gbp,
    a.n_descriptions,
    -- Flagged, not resolved: a moved label is a data-quality question.
    a.n_descriptions > 1 as has_multiple_descriptions,
    a.item_type <> 'product' as is_non_product
from activity as a
left join labels as l on a.stock_code = l.stock_code
