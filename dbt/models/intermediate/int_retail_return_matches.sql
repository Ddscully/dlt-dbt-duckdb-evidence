-- Every product return line, joined to the sale it most likely reverses.
-- Grain: one row per product return line (a negative-quantity product line on a
-- cancellation invoice), matched or not.
--
-- The source has no link between a return and its order, so the join is
-- inferred — a separate step from the reporting on it: `fct_retail_returns`
-- classifies the result and measures how often it failed.
--
-- The rule: the same customer's most recent prior purchase of the same product,
-- as an `asof join`. It cannot reach two kinds of line, and both are kept with a
-- null match rather than dropped, which would flatter the match rate: returns
-- with no customer id, and returns of goods bought before the extract opens
-- (2009-12-01) — left-censoring, not a defect.
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

-- Every line a return could point back at: a product sale, positive quantity,
-- named customer — 802,716 lines.
--
-- `quantity > 0` excludes stock write-offs (negative quantities on sale
-- invoices). Every write-off today is anonymous, so the clause moves no row, but
-- a write-off with a customer would otherwise become a return's "purchase". The
-- unit test `return_matches_never_point_at_a_stock_write_off` is its only guard.
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

-- One candidate per (customer, product, instant). An `asof join` over rows tied
-- on its inequality key picks one arbitrarily — DuckDB's parallel join draws a
-- different one per run — and 33,518 groups here are tied, covering 70,174 of
-- the 802,716 purchase lines; 604 return lines land on one. Without this the
-- model was not reproducible between builds.
--
-- A separate CTE because it is a separate population: 766,060 lines, one per
-- tie group, so 36,656 purchase lines (4.6%) are not here. A `purchases` CTE
-- quietly meaning "some of the purchases" would short any count or denominator
-- later computed from it.
--
-- The tie-break (lowest invoice, then line number; lexicographic) is arbitrary
-- but fixed — it does not claim to pick the better match. One line, not the
-- tied lines' sum: summing would redefine `original_quantity` and leave
-- `original_line_number` pointing at nothing. The cost: of the 604 tied matches,
-- 63 are flagged 'matched, quantity exceeds purchase' and 56 would be plain
-- matches if the tied lines were summed, so that bucket (366 rows) is an upper
-- bound on the rule picking the wrong sale.
--
-- This comment is the one copy of these figures; `_retail.yml`, the unit tests
-- and the `retail-models` and `unit-testing-dbt-models` skills cite it.
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
