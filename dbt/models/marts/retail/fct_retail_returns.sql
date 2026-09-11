-- Returns, matched back to the sale they reverse, with the match classified.
-- Grain: one row per product return line, matched or not.
--
-- The match is inferred in `int_retail_return_matches` (the same customer's most
-- recent prior purchase of the product); this model decides what counts as a
-- match and reports how often it failed:
--
--   * **352 lines have no customer id** and cannot be matched; they stay, as
--     `match_status = 'no customer id'`, rather than flattering the rate.
--   * **A return can predate the extract's purchase**, when the goods were
--     bought before 2009-12-01 — left-censoring, the largest category of miss.
--   * **Quantity is checked, not required.** A partial return is a match; a
--     return larger than the purchase suggests the wrong sale
--     (`quantity_is_consistent`).
--
-- Over 18,286 lines: 87.7% match cleanly, 2.0% match a smaller purchase, 8.4%
-- have no prior purchase in the window and 1.9% no customer. The 2.0% is the
-- rule being wrong rather than the data absent, and is an upper bound — see
-- `int_retail_return_matches`. The median return comes back 10 days after
-- purchase, the plausible shape; arbitrary matches would spread flat.
with matched as (
    select * from {{ ref('int_retail_return_matches') }}
),

-- For `date_key`, as `fct_retail_order_line` does at the same grain. Inner:
-- `dim_date` spans every invoice date, so it drops nothing (measured).
calendar as (
    select * from {{ ref('dim_date') }}
)

select
    r.invoice,
    r.line_number,
    r.customer_id,
    r.stock_code,
    r.description,
    r.country,
    r.country_iso3,
    r.invoice_ts,
    r.invoice_date,
    r.invoice_month,
    -- The return's own date. One key: `original_invoice_date` is a second role
    -- on the dimension and would need another name, which the bus matrix would
    -- not read as conformance.
    d.date_key,
    r.quantity_returned,
    r.unit_price,
    r.return_amount_gbp,
    r.original_invoice,
    r.original_line_number,
    r.original_invoice_date,
    r.original_quantity,
    r.original_unit_price,
    r.original_amount_gbp,
    -- Null when unmatched: unknown, not zero.
    date_diff('day', r.original_invoice_date, r.invoice_date) as days_to_return,
    r.original_invoice is not null as is_matched,
    case
        when r.customer_id is null then 'no customer id'
        when r.original_invoice is null then 'no prior purchase in window'
        when r.quantity_returned > r.original_quantity then 'matched, quantity exceeds purchase'
        else 'matched'
    end as match_status,
    -- Null, not false, when unmatched: there is nothing to be consistent with.
    case
        when r.original_invoice is not null then r.quantity_returned <= r.original_quantity
    end as quantity_is_consistent,
    -- A wrong match, or a refund at a different price; either is worth a look.
    case
        when r.original_unit_price > 0 then r.unit_price <> r.original_unit_price
    end as price_differs_from_original
from matched as r
inner join calendar as d on r.invoice_date = d.date_day
