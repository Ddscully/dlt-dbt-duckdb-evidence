-- EU household electricity prices at Eurostat's own half-year grain. Use this
-- for prices over time and `fct_emissions_energy`'s annual column only to join
-- prices to emissions or GDP (see _country_stats.yml).
with semiannual as (
    select * from {{ ref('stg_eu_electricity_prices_semiannual') }}
),

spine as (
    select * from {{ ref('dim_country_year') }}
),

-- USD per EUR at Eurostat's half-year grain.
usd as (
    select
        period_start_date,
        avg_units_per_eur,
        period_end_units_per_eur,
        period_is_complete
    from {{ ref('fct_fx_rates_periods') }}
    where period_type = 'half' and currency_code = 'USD'
),

-- Half-over-half change, only where the previous row is exactly six months back
-- (`lag` returns the previous row the country has, and some have gaps).
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
    -- Shipped, not used: the closing rate suits a balance, not a price.
    f.period_end_units_per_eur as usd_per_eur_period_end,
    not f.period_is_complete as usd_conversion_is_partial_period
from with_change as s
inner join spine as d on s.country_iso3 = d.country_iso3 and s.year = d.year
left join usd as f on s.period_start_date = f.period_start_date
