-- Order lines, priced in three currencies and joined to the calendar.
-- Grain: one row per (invoice, line_number) — the warehouse's finest grain.
--
--   * **Converted at the transaction date's rate.** A line is a flow at an
--     instant, so the daily fixing is its rate; the spot-or-average question of
--     `fct_fx_rates_periods` arises only on aggregation, and summing converted
--     lines answers it.
--   * **The rate may be carried forward, and the row says so.** 139,658 lines
--     (13.1%) fall on a day the ECB did not quote, so `fx_rate_source_date` and
--     `fx_rate_is_carried_forward` travel with the amount. Every one is a
--     Sunday: this business trades on Sundays (139,256 lines, against 402 on
--     Saturdays) and closes on the TARGET holidays.
with lines as (
    select * from {{ ref('stg_retail_lines') }}
),

calendar as (
    select * from {{ ref('dim_date') }}
),

-- GBP and USD rates on the transaction date: one join per currency.
gbp as (
    select
        date_day,
        eur_per_unit,
        rate_source_date,
        is_carried_forward
    from {{ ref('fct_fx_rates_daily') }}
    where currency_code = 'GBP'
),

usd as (
    select
        date_day,
        units_per_eur
    from {{ ref('fct_fx_rates_daily') }}
    where currency_code = 'USD'
)

select
    l.invoice,
    l.line_number,
    l.invoice_type,
    l.item_type,
    l.stock_code,
    l.description,
    l.customer_id,
    l.country,
    -- The conformed country key (resolved in staging), which joins retail to
    -- the country domain.
    l.country_iso3,
    l.invoice_ts,
    l.invoice_date,
    l.invoice_month,
    -- Calendar columns from the dimension, not re-derived here.
    d.date_key,
    -- Both years: they agree on every row here only because the business closes
    -- over New Year, when ISO week 1 can cross the calendar year.
    d.year,
    d.iso_year,
    d.iso_week,
    d.iso_week_start_date,
    d.day_name,
    d.is_weekend,
    d.fiscal_year,
    d.fiscal_quarter,
    l.quantity,
    l.unit_price,
    l.line_amount_gbp,
    -- Null when no rate can be carried — never for this period, but nullable
    -- because the model must not assume it (`_retail.yml` tests it).
    l.line_amount_gbp * g.eur_per_unit as line_amount_eur,
    l.line_amount_gbp * g.eur_per_unit * u.units_per_eur as line_amount_usd,
    g.eur_per_unit as eur_per_gbp,
    g.rate_source_date as fx_rate_source_date,
    g.is_carried_forward as fx_rate_is_carried_forward,
    l.is_revenue_line,
    l.is_stock_write_off
from lines as l
inner join calendar as d on l.invoice_date = d.date_day
left join gbp as g on l.invoice_date = g.date_day
left join usd as u on l.invoice_date = u.date_day
