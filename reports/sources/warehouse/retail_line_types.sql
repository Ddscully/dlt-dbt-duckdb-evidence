-- Every (invoice type, item type) combination the extract contains, which is
-- the page's evidence that neither classification is decoration. A model that
-- read "negative quantity" as "return" would fold the sale/stock-write-off rows
-- into the cancellation ones; a revenue figure that ignored item_type would
-- carry GBP 464k of postage and -GBP 339k of bank fees as if they were sales.
select
    invoice_type,
    item_type,
    count(*)                                            as n_lines,
    count(distinct invoice)                             as n_invoices,
    sum(quantity)                                       as units,
    sum(line_amount_gbp)                                as amount_gbp,
    count(*) filter (where is_stock_write_off)          as n_write_offs,
    count(*) filter (where customer_id is null)         as n_anonymous
from marts.fct_retail_order_line
group by invoice_type, item_type
