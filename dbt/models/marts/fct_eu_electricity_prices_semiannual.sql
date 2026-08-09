-- EU household electricity prices at Eurostat's own grain.
-- Grain: one row per (country_iso3, year, half).
--
-- The one fact here that isn't annual. It exists because averaging the two
-- half-years — which `fct_emissions_energy.electricity_price_eur_kwh` does, and
-- has to, to sit on the country-year spine — erases the sharpest price movement
-- in the series. Reach for this model for anything about prices *over time*, and
-- the annual column for anything joining prices to emissions or GDP.
--
-- **It is also the warehouse's one genuinely mixed-currency question**, which is
-- why the USD columns hang off this model and not another. Everything else here
-- is denominated in US dollars (the World Bank's GDP series) or in nothing at
-- all (tonnes, kWh); this is the only euro-denominated measurement, so before
-- `fct_fx_rates_periods` existed a euro price and a dollar GDP simply could not
-- be put in the same sentence.
--
-- A price over a half-year is a **flow**, so it converts at the period average
-- and not at the closing rate — see the header of `fct_fx_rates_periods`. The
-- rate used ships beside the converted number, and so does the closing rate that
-- was *not* used, because a converted figure whose rate you can't see is a
-- figure nobody can check.
with semiannual as (
    select * from {{ ref('stg_eu_electricity_prices_semiannual') }}
),

spine as (
    select * from {{ ref('dim_country_year') }}
),

-- USD per EUR at Eurostat's own half-year grain — the join `dim_date`'s
-- Eurostat-shaped `half` column exists to make possible.
usd as (
    select
        period_start_date,
        avg_units_per_eur,
        period_end_units_per_eur,
        period_is_complete
    from {{ ref('fct_fx_rates_periods') }}
    where period_type = 'half' and quote_currency = 'USD'
),

-- Half-over-half change. `lag` returns the previous row this country *has*, which
-- is not always the previous half — countries enter the series at different dates
-- and a few have gaps — so the change is only reported when the preceding row is
-- exactly six months back, and is null at the start of each country's series.
with_change as (
    select
        country_iso3,
        year,
        half,
        period,
        period_start_date,
        electricity_price_eur_kwh,
        lag(electricity_price_eur_kwh) over country_periods as previous_price,
        lag(period_start_date) over country_periods
        = period_start_date - interval 6 month as follows_previous_half
    from semiannual
    window country_periods as (
        partition by country_iso3
        order by period_start_date
    )
)

select
    s.country_iso3,
    d.country_name,
    d.region,
    d.income_group,
    s.year,
    s.half,
    s.period,
    s.period_start_date,
    -- Eurostat nrg_pc_204: household price, EUR/kWh, all taxes included
    s.electricity_price_eur_kwh,
    case
        when s.follows_previous_half
            then s.electricity_price_eur_kwh - s.previous_price
    end as change_vs_previous_half_eur_kwh,
    case
        when s.follows_previous_half and s.previous_price > 0
            then (s.electricity_price_eur_kwh - s.previous_price) / s.previous_price * 100
    end as change_vs_previous_half_pct,

    -- The same price in dollars, at the average rate over the same half-year.
    s.electricity_price_eur_kwh * f.avg_units_per_eur as electricity_price_usd_kwh,
    f.avg_units_per_eur as usd_per_eur_period_avg,
    -- Shipped, not used: the closing rate is the right one for a balance and the
    -- wrong one for a price, and having both in the table is how that stays a
    -- visible choice rather than a buried one.
    f.period_end_units_per_eur as usd_per_eur_period_end,
    not f.period_is_complete as usd_conversion_is_partial_period
from with_change as s
inner join spine as d on s.country_iso3 = d.country_iso3 and s.year = d.year
left join usd as f on s.period_start_date = f.period_start_date
