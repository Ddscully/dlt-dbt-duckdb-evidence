-- Order lines, priced in three currencies and joined to the calendar.
-- Grain: one row per (invoice, line_number) — the warehouse's finest grain, and
-- the first fact in it that records something a person did rather than something
-- a statistical agency published.
--
-- **This is where `dim_date` and the FX tables stop being a demonstration.**
-- Every other model in the warehouse is annual and denominated in whatever unit
-- its publisher chose. This one is a GBP amount stamped with a timestamp, which
-- is the exact shape that needs a calendar to group by and a rate to convert
-- with — so idea 22's four tables are load-bearing here rather than decorative.
--
-- Two decisions worth stating, because both are easy to get wrong invisibly:
--
--   * **The conversion is at the transaction date's rate, not at a period
--     average.** An order line is a flow, but it is a flow that happened at an
--     instant, so the daily fixing *is* its rate — the spot-vs-average question
--     `fct_fx_rates_periods` exists for arises when you aggregate, and it is
--     answered by summing the converted lines rather than converting the sum.
--     Both are shipped so the difference can be measured, which is the point.
--   * **The rate is carried forward, and the row says when it was published.**
--     A third of these orders fall on a day the ECB did not quote — Saturdays
--     especially, this being a retailer — so `fx_rate_source_date` and
--     `fx_rate_is_carried_forward` travel with the amount. A converted figure
--     whose provenance is one join away is a figure nobody can audit.
with lines as (
    select * from {{ ref('stg_retail_lines') }}
),

calendar as (
    select * from {{ ref('dim_date') }}
),

-- GBP per EUR, and USD per EUR, on the transaction date. Two separate joins
-- rather than one pivot: the currency panel is a dimension, and a pivot here
-- would need editing every time somebody wants a third currency.
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
    l.invoice_ts,
    l.invoice_date,
    l.invoice_month,
    -- The calendar columns come from the dimension rather than being re-derived
    -- with `date_part` here, which is the whole reason a date dimension exists:
    -- one definition of "which week is this" for every fact that ever asks.
    d.date_key,
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
    -- EUR and USD at the transaction date's fixing. Null when no rate can be
    -- carried (never, for GBP over this period — but the column is nullable
    -- because the *model* must not assume that, and `_marts.yml` tests it).
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
