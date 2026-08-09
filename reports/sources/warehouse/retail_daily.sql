-- Daily aggregate of the order-line fact. The fact itself is 1.07M rows, which
-- is far too much to ship to a browser as Parquet — this is the roll-up the
-- page's time series and weekday charts read.
--
-- `day_name` and `is_weekend` come from dim_date via the fact, not from a
-- date_part re-derived here, which is the point of having a date dimension.
select
    invoice_date,
    year,
    invoice_month,
    day_name,
    is_weekend,
    count(*)                                            as n_lines,
    count(distinct invoice)                             as n_invoices,
    count(distinct customer_id)                         as n_customers,
    sum(line_amount_gbp) filter (where is_revenue_line) as revenue_gbp,
    sum(line_amount_eur) filter (where is_revenue_line) as revenue_eur,
    max(eur_per_gbp)                                    as eur_per_gbp,
    bool_or(fx_rate_is_carried_forward)                 as fx_rate_is_carried_forward
from marts.fct_retail_order_line
group by invoice_date, year, invoice_month, day_name, is_weekend
