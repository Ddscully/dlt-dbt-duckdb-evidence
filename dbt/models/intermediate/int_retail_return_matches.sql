-- Every return line, joined to the sale it most likely reverses.
-- Grain: one row per cancellation line — every one of them, matched or not.
--
-- **The source has no link between a return and its original order.** There is
-- no order reference, no RMA number, no line pointer; there is a `C`-prefixed
-- invoice with a negative quantity and the expectation that you work it out.
-- That is the ordinary condition of transactional data and it is why this model
-- exists — the join has to be *inferred*, and inference is a different kind of
-- thing from the reporting built on top of it, which is why it is its own model.
-- `fct_retail_returns` classifies what happened here and measures how often it
-- failed; nothing but arithmetic happens between the two.
--
-- The rule: the same customer's most recent prior purchase of the same product.
-- An `asof join`, because "most recent before" is exactly what it expresses and
-- a correlated subquery over 1M rows is not.
--
-- The rule cannot reach two kinds of line, and both are kept as rows with a null
-- match rather than dropped — dropping them would quietly improve the match rate
-- by removing the hardest cases. Return lines with no customer id have nothing
-- to match *on*; and a return can precede its purchase in this window, because
-- the extract opens on 2009-12-01 and goods bought in November 2009 come back in
-- December. That is left-censoring, not a defect. `fct_retail_returns` names
-- both, counts them, and carries the measured distribution.
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
-- an unstable one. But it is not free: of the 604 tied matches, 63 are flagged
-- 'matched, quantity exceeds purchase' and **56 of them would be plain matches
-- if the tied lines were added up** — the customer did buy that many, across
-- two lines of one order. That is 15% of the 366 rows in the bucket
-- `fct_retail_returns` calls its most interesting number, so the bucket is an
-- upper bound on "the rule found the wrong sale" rather than a count of it.
-- Left as a separate decision on purpose; a determinism fix should not quietly
-- move 56 rows between buckets.
--
-- These five figures are measured against the warehouse this model *now*
-- builds. The first version of this comment quoted 70 / 63 / 367, taken while
-- diagnosing the instability — i.e. from the model that gave a different answer
-- every build. A determinism fix invalidates the evidence gathered for it, so
-- everything here was re-measured afterwards. This comment is the one copy that
-- carries the numbers; CLAUDE.md and the retail skill cite it.
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
asof left join purchases as p
    on
        r.customer_id = p.customer_id
        and r.stock_code = p.stock_code
        and r.invoice_ts >= p.invoice_ts
