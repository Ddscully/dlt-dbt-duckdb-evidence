-- One row per stock code the retailer ever transacted.
-- Grain: `stock_code`.
--
-- The interesting column is `description`, and the reason is survivorship. 1,192
-- of the 5,131 codes carry more than one description and one carries nine
-- — variant spellings, corrections, and the odd line where the field was used as
-- a note ("wrong barcode", "damages"). A dimension has to pick one, and *which
-- rule picks it* is a decision with a visible consequence on every chart the
-- product name appears on.
--
-- The rule here is modal-then-alphabetical: the description used on the most
-- lines wins, ties broken by sorting. Two properties earn it — it is
-- deterministic (a rebuild picks the same label, so the diff between two builds
-- means something) and it prefers the label the business actually used over the
-- most recent one, which on this source is frequently a one-off correction note
-- rather than the product's name.
--
-- `item_type` comes from `stg_retail_lines` and is constant per code by
-- construction, so it is an `any_value` rather than a mode — and `_marts.yml`
-- tests that it really is constant, because "constant by construction" is the
-- kind of claim that stops being true when somebody edits the CASE expression.
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
        sum(quantity) filter (where invoice_type = 'cancellation') as units_returned,
        sum(line_amount_gbp) filter (where is_revenue_line) as net_revenue_gbp,
        -- Median rather than mean: this retailer discounts heavily and sells the
        -- same SKU at a wholesale and a retail price, so the mean sits between
        -- two prices that both exist and lands on one that doesn't.
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
    -- Flagged rather than resolved. A code whose label moved is a candidate for
    -- a data-quality conversation, not something a mart should quietly smooth
    -- over — and at 1,192 codes it is a real backlog, not a curiosity.
    a.n_descriptions > 1 as has_multiple_descriptions,
    a.item_type <> 'product' as is_non_product
from activity as a
left join labels as l on a.stock_code = l.stock_code
