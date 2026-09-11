-- UCI Online Retail II order lines, cleaned and classified.
-- Grain: one row per (invoice, line_number) — unchanged from the source.
--
-- No rows added or removed, and no money computed: this names distinctions the
-- source encodes and never documents, each a place a naive read goes wrong.
--
--   * **Three invoice prefixes.** 45,330 invoices are sales, 8,292 carry `C`
--     (cancellation) and 6 carry `A` — bad-debt adjustments worth -GBP 147,614,
--     and the only negative prices.
--   * **A negative quantity is not always a return.** 3,457 negative-quantity
--     lines on *sale* invoices, all zero-priced and anonymous, are inventory
--     write-offs posted through the transaction table.
--   * **`stock_code` is not only products.** Postage, fees, vouchers, samples,
--     manual adjustments and `TEST001` share the column (AMAZONFEE alone is
--     -GBP 260,764), so `item_type` says which a revenue figure includes.
--
-- `is_revenue_line` is the one opinion: a product sale or return a customer was
-- charged for. Everything else stays, flagged.
--
-- It also resolves the country label to `country_iso3` through the
-- `retail_country_map` seed, since 9 of the 43 labels differ from the
-- dimension's names and a join on name would silently lose them.
with source as (
    select * from {{ source('raw', 'retail_invoice_lines') }}
),

country_map as (
    select * from {{ ref('retail_country_map') }}
),

renamed as (
    select
        invoice,
        line_number,
        -- Upper-cased: many codes differ only by case (`M` and `m` are one
        -- manual adjustment), and would otherwise be separate products.
        upper(trim(stock_code)) as stock_code,
        -- Nulls kept (4,382); `dim_retail_product` picks one label per code.
        nullif(trim(description), '') as description,
        quantity,
        unit_price,
        -- A blank customer (22.8% of lines) is a real, signed-out sale, nulled
        -- so it joins as an absence rather than an empty-string customer.
        nullif(trim(customer_id), '') as customer_id,
        -- Unreachable today (the workbook read yields NULL for an empty cell),
        -- but an empty string would join to the seed as an unknown 44th label.
        -- A unit test in `_unit_tests.yml` pins it.
        nullif(trim(country), '') as country,
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
            -- Specific codes first; everything else is a product (`DCGS0058` is,
            -- `TEST001` is not, though both start with letters).
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
),

-- **A left join, never inner.** A null code means one of the three non-country
-- labels (`European Community`, `West Indies`, `Unspecified`) or a label the
-- seed has never seen, which the `relationships` test in `_staging.yml` names.
-- An inner join would delete those rows and that test would then pass. The unit
-- test `..._keeps_a_line_whose_country_the_map_has_never_seen` guards it.
resolved as (
    select
        c.*,
        m.country_iso3
    from classified as c
    left join country_map as m on c.country = m.retail_country
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
    -- Signed, so net revenue is a single `sum`.
    quantity * unit_price as line_amount_gbp,
    customer_id,
    country,
    -- The conformed key, beside the source's label: the 871 lines (GBP 11,515)
    -- whose label is an aggregate or an absence resolve to no code.
    country_iso3,
    invoice_ts,
    invoice_date,
    invoice_month,
    -- Negative quantity on a sale invoice. `_staging.yml` tests that none
    -- carries a price, so a priced one fails rather than being booked.
    invoice_type = 'sale' and quantity < 0 as is_stock_write_off,
    -- Products only, sales and cancellations, no write-offs or adjustments:
    -- sums to revenue net of returns.
    item_type = 'product'
    and not (invoice_type = 'sale' and quantity < 0)
    and invoice_type <> 'adjustment' as is_revenue_line
from resolved
