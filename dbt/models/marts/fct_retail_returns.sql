-- Returns, matched back to the sale they reverse.
-- Grain: one row per cancellation line — every one of them, matched or not.
--
-- **The source has no link between a return and its original order.** There is
-- no order reference, no RMA number, no line pointer; there is a `C`-prefixed
-- invoice with a negative quantity and the expectation that you work it out.
-- That is the ordinary condition of transactional data and it is why this model
-- exists — the join has to be *inferred*, and inferring it means deciding what
-- counts as a match and then reporting how often the decision failed.
--
-- The rule: the same customer's most recent prior purchase of the same product.
-- An `asof join`, because "most recent before" is exactly what it expresses and
-- a correlated subquery over 1M rows is not.
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
-- rather than the data being absent.
--
-- The distribution is a sanity check in itself and it passes: the median return
-- comes back **10 days** after purchase, the mean is 32, and 1,585 come back the
-- same day. A matching rule that had latched onto arbitrary sales would produce
-- a flat spread over the two-year window instead of that shape.
with lines as (
    select * from {{ ref('stg_retail_lines') }}
),

-- Only genuine product returns. A cancellation of a postage line or a manual
-- adjustment is a correction, not a customer sending something back, and mixing
-- them in is how a return rate ends up above what was ever sold.
returns as (
    select
        invoice,
        line_number,
        customer_id,
        stock_code,
        description,
        country,
        invoice_ts,
        invoice_date,
        invoice_month,
        -- Flipped to positive here: a returned quantity of 4 reads better than
        -- -4 everywhere downstream, and the signed amount stays signed.
        -quantity as quantity_returned,
        unit_price,
        line_amount_gbp as return_amount_gbp
    from lines
    where
        invoice_type = 'cancellation'
        and item_type = 'product'
        and quantity < 0
),

-- One candidate per (customer, product, instant), because an `asof join` that
-- has several rows tied on its inequality key picks one of them arbitrarily —
-- and 33,518 groups here are tied, covering 70,174 of the 802,716 purchase
-- lines (8.7%). 604 return lines (3.68%) land on one of those groups, up to 20
-- deep, and before this the model was **not reproducible between builds**:
-- three consecutive runs against byte-identical sources gave 16,031 / 16,032 /
-- 16,030 clean matches and `sum(original_quantity)` of 637,411 / 636,410 /
-- 636,208. `dim_retail_customer` had already met this and settled it the same
-- way, ranking on `min(invoice_ts)` then `invoice`.
--
-- The tie-break is the lowest invoice then the lowest line number. `invoice` is
-- a string, so that ordering is lexicographic rather than numeric — which is
-- fine for the job, since the point is that the choice is *fixed*, not that it
-- is meaningful. Nothing here claims the chosen line is the better match.
--
-- **Deliberately one line and not their sum**, and the cost of that is
-- measured rather than waved past. Summing the tied lines would change what
-- `original_quantity` means and leave `original_line_number` with nothing to
-- point at, so it is a re-specification of the matching rule and not a fix for
-- an unstable one. But it is not free: of the 604 tied matches, 70 are flagged
-- 'matched, quantity exceeds purchase' and **63 of them would be plain matches
-- if the tied lines were added up** — the customer did buy that many, across
-- two lines of one order. That is 17% of the 367 rows in the bucket this model
-- calls its most interesting number, so the bucket is an upper bound on "the
-- rule found the wrong sale" rather than a count of it. Left as a separate
-- decision on purpose; a determinism fix should not quietly move 63 rows
-- between buckets.
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
    qualify row_number() over (
        partition by customer_id, stock_code, invoice_ts
        order by invoice, line_number
    ) = 1
),

matched as (
    select
        r.*,
        p.invoice as original_invoice,
        p.line_number as original_line_number,
        p.invoice_date as original_invoice_date,
        p.quantity as original_quantity,
        p.unit_price as original_unit_price,
        p.line_amount_gbp as original_amount_gbp
    from returns as r
    asof left join purchases as p
        on
            r.customer_id = p.customer_id
            and r.stock_code = p.stock_code
            and r.invoice_ts >= p.invoice_ts
)

select
    invoice,
    line_number,
    customer_id,
    stock_code,
    description,
    country,
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
