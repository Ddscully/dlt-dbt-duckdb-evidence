-- Returns, matched back to the sale they reverse, with the match classified.
-- Grain: one row per cancellation line — every one of them, matched or not.
--
-- The matching itself is `int_retail_return_matches`: the source carries no link
-- between a return and its original order, so the join is *inferred* from the
-- same customer's most recent prior purchase of the same product. This model is
-- the other half of that — deciding what counts as a match and then reporting
-- how often the decision failed.
--
-- What the rule cannot do, stated rather than hidden:
--
--   * **352 return lines have no customer id at all** and are structurally
--     unmatchable — there is nothing to match *on*. They are kept as rows with a
--     null match and `match_status = 'no customer id'`, because dropping them
--     would quietly improve the match rate by removing the hardest cases.
--   * **A return can precede its purchase in this window.** The extract opens on
--     2009-12-01 and goods bought in November 2009 come back in December, so the
--     original is simply not in the file. That is left-censoring, not a defect,
--     and it is the largest single category of miss.
--   * **Quantity is checked, not required.** A partial return (bought 12,
--     returned 4) is a genuine match; a return larger than the purchase it
--     points at is a sign the rule found the wrong sale. `quantity_is_consistent`
--     separates the two instead of a bare boolean pretending it can't happen.
--
-- Measured, over 18,286 return lines: **87.6% match cleanly**, 2.0% match but to
-- a purchase smaller than the return, 8.4% have no prior purchase in the window
-- and 1.9% have no customer id. Among the lines that *can* be matched at all the
-- rate is 91.4%. The honest way to read the remainder is as the cost of a source
-- without a foreign key, not as a bug to be tuned away — and the 2.0% is the
-- more interesting number than the 87.6%, because it is the rule being wrong
-- rather than the data being absent. That bucket is an upper bound rather than a
-- count; `int_retail_return_matches` measures why.
--
-- The distribution is a sanity check in itself and it passes: the median return
-- comes back **10 days** after purchase, the mean is 32, and 1,585 come back the
-- same day. A matching rule that had latched onto arbitrary sales would produce
-- a flat spread over the two-year window instead of that shape.
with matched as (
    select * from {{ ref('int_retail_return_matches') }}
)

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
    quantity_returned,
    unit_price,
    return_amount_gbp,
    original_invoice,
    original_line_number,
    original_invoice_date,
    original_quantity,
    original_unit_price,
    original_amount_gbp,
    -- Days on the shelf before it came back. Null when unmatched, which is the
    -- one place a null here means "unknown" rather than "not applicable".
    date_diff('day', original_invoice_date, invoice_date) as days_to_return,
    original_invoice is not null as is_matched,
    case
        when customer_id is null then 'no customer id'
        when original_invoice is null then 'no prior purchase in window'
        when quantity_returned > original_quantity then 'matched, quantity exceeds purchase'
        else 'matched'
    end as match_status,
    -- Null rather than false when unmatched: there is no quantity to be
    -- consistent *with*, and a false here would read as a failed check.
    case
        when original_invoice is not null then quantity_returned <= original_quantity
    end as quantity_is_consistent,
    -- A price that moved between the sale and the return is worth seeing: it
    -- either means the rule matched the wrong sale, or the item was refunded at
    -- a different price than it was bought at. Both are worth a question.
    case
        when original_unit_price > 0 then unit_price <> original_unit_price
    end as price_differs_from_original
from matched
