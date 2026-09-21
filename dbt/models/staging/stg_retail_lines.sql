-- UCI Online Retail II order lines, cleaned and classified, at (invoice,
-- line_number). No rows added or removed: it names what the source never
-- documents, and each column's description in _staging.yml says what.
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
        upper(trim(stock_code)) as stock_code,
        -- Nulls kept (4,382); `dim_retail_product` picks one label per code.
        nullif(trim(description), '') as description,
        quantity,
        unit_price,
        -- A blank customer is a signed-out sale: an absence, not a customer.
        nullif(trim(customer_id), '') as customer_id,
        -- Unreachable today, but an empty string would join the seed as a 44th
        -- label; a unit test pins it.
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

-- **A left join, never inner**: an inner join deletes the unmapped labels before
-- the `relationships` test on `country` can name them (retail-models skill).
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
    quantity * unit_price as line_amount_gbp,
    customer_id,
    country,
    country_iso3,
    invoice_ts,
    invoice_date,
    invoice_month,
    invoice_type = 'sale' and quantity < 0 as is_stock_write_off,
    item_type = 'product'
    and not (invoice_type = 'sale' and quantity < 0)
    and invoice_type <> 'adjustment' as is_revenue_line
from resolved
