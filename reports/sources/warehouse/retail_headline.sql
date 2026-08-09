-- One row: the whole retail extract's totals, plus the two splits the page
-- argues about — how much revenue has no customer attached to it, and how much
-- of it converts on a rate the ECB published on a different day.
--
-- `is_revenue_line` is doing real work here. A bare sum over line_amount_gbp
-- mixes in postage, bank fees and the six bad-debt adjustments; see the
-- retail_line_types source and the note in stg_retail_lines.
select
    count(*)                                                       as n_lines,
    count(distinct invoice)                                        as n_invoices,
    count(distinct customer_id)                                    as n_customers,
    count(distinct stock_code)                                     as n_products,
    min(invoice_date)                                              as first_day,
    max(invoice_date)                                              as last_day,
    sum(line_amount_gbp) filter (where is_revenue_line)            as revenue_gbp,
    sum(line_amount_eur) filter (where is_revenue_line)            as revenue_eur,
    sum(line_amount_usd) filter (where is_revenue_line)            as revenue_usd,
    count(*) filter (where customer_id is null)                    as n_lines_anonymous,
    sum(line_amount_gbp) filter (
        where is_revenue_line and customer_id is null
    )                                                              as revenue_gbp_anonymous,
    count(*) filter (where fx_rate_is_carried_forward)             as n_lines_fx_carried
from marts.fct_retail_order_line
