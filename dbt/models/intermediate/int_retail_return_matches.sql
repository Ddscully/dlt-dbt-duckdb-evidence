-- Every product return line, joined to the sale it most likely reverses, matched
-- or not. The rule, what it cannot reach and the tie-break's cost are the
-- description in _intermediate.yml.
with lines as (
    select * from {{ ref('stg_retail_lines') }}
),

-- Product returns only: cancelling a postage line or a manual adjustment is a
-- correction, not a customer sending something back.
returns as (
    select
        invoice,
        line_number,
        customer_id,
        stock_code,
        description,
        country,
        country_iso3,
        invoice_ts,
        invoice_date,
        invoice_month,
        -- Positive quantity downstream; the amount stays signed.
        -quantity as quantity_returned,
        unit_price,
        line_amount_gbp as return_amount_gbp
    from lines
    where
        invoice_type = 'cancellation'
        and item_type = 'product'
        and quantity < 0
),

-- Every line a return could point back at. `quantity > 0` excludes stock
-- write-offs; it moves no row today, so a unit test is its only guard.
purchases as (
    select
        invoice,
        line_number,
        customer_id,
        stock_code,
        invoice_ts,
        invoice_date,
        quantity,
        unit_price,
        line_amount_gbp
    from lines
    where
        invoice_type = 'sale'
        and item_type = 'product'
        and quantity > 0
        and customer_id is not null
),

-- One candidate per (customer, product, instant): an `asof join` picks among
-- ties arbitrarily, per run. A separate CTE because it is a separate population.
match_candidates as (
    select * from purchases
    qualify row_number() over (
        partition by customer_id, stock_code, invoice_ts
        order by invoice, line_number
    ) = 1
)

select
    r.*,
    p.invoice as original_invoice,
    p.line_number as original_line_number,
    p.invoice_date as original_invoice_date,
    p.quantity as original_quantity,
    p.unit_price as original_unit_price,
    p.line_amount_gbp as original_amount_gbp
from returns as r
asof left join match_candidates as p
    on
        r.customer_id = p.customer_id
        and r.stock_code = p.stock_code
        and r.invoice_ts >= p.invoice_ts
