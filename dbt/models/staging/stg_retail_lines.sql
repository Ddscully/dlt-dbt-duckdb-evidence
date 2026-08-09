-- UCI Online Retail II order lines, cleaned and classified.
-- Grain: one row per (invoice, line_number) — unchanged from the source.
--
-- This model adds no rows, removes no rows and computes no money. All it does is
-- give names to distinctions the source encodes and never documents, because
-- every one of them is a place a naive read gets a wrong number:
--
--   * **Three invoice prefixes, not two.** 45,330 invoices are plain sales,
--     8,292 carry a `C` (cancellation), and 6 carry an `A` — bad-debt
--     adjustments, worth -GBP 147,614 between them. `A` is the one nobody
--     expects, it is the only place a *negative price* occurs, and at six rows
--     it survives no sampling. Left in `raw` and named here.
--   * **A negative quantity is not a return.** 3,457 negative-quantity lines sit
--     on *sale* invoices, and every single one has a zero price and no customer:
--     they are inventory write-offs ("faulty", "crushed ctn", "given away",
--     "wrong barcode") posted through the transaction table. Counting them as
--     returns overstates the return count by 3,457 and the returned value by
--     exactly GBP 0, which is the tell.
--   * **`stock_code` is not only products.** Postage, carriage, bank charges,
--     Amazon fees, CRUK commission, gift vouchers, samples, manual adjustments
--     and a literal `TEST001` all arrive in the same column as the mugs.
--     AMAZONFEE alone is -GBP 260,764. Any revenue figure has to say which of
--     these it includes, so `item_type` exists to let it.
--
-- `is_revenue_line` is the one opinion in the file: a product sale or return
-- that a customer was actually charged for. Everything else stays, flagged, so
-- the alternative definitions are a `where` clause rather than a rebuild.
with source as (
    select * from {{ source('raw', 'retail_invoice_lines') }}
),

renamed as (
    select
        invoice,
        line_number,
        -- Upper-cased because 173 codes differ from another only by case — `M`
        -- and `m` are the same manual adjustment — and a dimension keyed on the
        -- raw value would carry both as separate products.
        upper(trim(stock_code)) as stock_code,
        -- Kept as sent, including the 4,382 nulls. `dim_retail_product` picks
        -- one label per code; a per-line coalesce here would invent text.
        nullif(trim(description), '') as description,
        quantity,
        unit_price,
        -- The blank customer is 22.8% of lines and is *not* a data error — it is
        -- a real sale nobody was signed in for. Nulled explicitly so it joins as
        -- an absence rather than as an empty-string customer.
        nullif(trim(customer_id), '') as customer_id,
        trim(country) as country,
        invoice_ts,
        cast(invoice_ts as date) as invoice_date,
        invoice_month
    from source
),

classified as (
    select
        *,
        case
            when starts_with(invoice, 'C') then 'cancellation'
            when starts_with(invoice, 'A') then 'adjustment'
            else 'sale'
        end as invoice_type,
        case
            -- Order matters: the specific codes are checked before the generic
            -- five-digit product pattern, because `DCGS0058` and `TEST001` both
            -- start with letters and only one of them is a product.
            when stock_code in ('POST', 'DOT', 'C2') then 'shipping'
            when stock_code in ('BANK CHARGES', 'AMAZONFEE', 'CRUK') then 'fee'
            when stock_code = 'D' then 'discount'
            when stock_code = 'S' then 'sample'
            when stock_code in ('M', 'B', 'ADJUST') then 'adjustment'
            when starts_with(stock_code, 'GIFT_') then 'voucher'
            when starts_with(stock_code, 'TEST') then 'test'
            else 'product'
        end as item_type
    from renamed
)

select
    invoice,
    line_number,
    invoice_type,
    stock_code,
    item_type,
    description,
    quantity,
    unit_price,
    -- Signed, always: a cancellation's negative quantity times a positive price
    -- is a negative amount, and that is what makes net revenue a `sum` rather
    -- than a `sum` minus another `sum` that someone will one day forget.
    quantity * unit_price as line_amount_gbp,
    customer_id,
    country,
    invoice_ts,
    invoice_date,
    invoice_month,
    -- A write-off, not a return: negative quantity on a *sale* invoice. The
    -- zero-price condition is asserted rather than assumed — `_staging.yml`
    -- tests that no such line has ever carried a price, so the day one does the
    -- classification fails loudly instead of quietly booking it as revenue.
    invoice_type = 'sale' and quantity < 0 as is_stock_write_off,
    -- The one opinion here. Excludes shipping, fees, discounts, samples,
    -- vouchers, the test SKU, manual adjustments and write-offs; includes both
    -- sales and genuine cancellations, so it sums to revenue net of returns.
    item_type = 'product'
    and not (invoice_type = 'sale' and quantity < 0)
    and invoice_type <> 'adjustment' as is_revenue_line
from classified
