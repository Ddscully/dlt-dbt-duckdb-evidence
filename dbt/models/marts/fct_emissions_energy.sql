-- Wide country-year fact joining emissions + energy + WDI onto the country dim.
-- Grain: one row per (country_iso3, year).
with co2 as (
    select * from {{ ref('stg_co2') }}
),

energy as (
    select * from {{ ref('stg_energy') }}
),

wdi as (
    select * from {{ ref('stg_wdi') }}
),

eu_prices as (
    select * from {{ ref('stg_eu_electricity_prices') }}
),

country as (
    select * from {{ ref('stg_country') }}
)

select
    c.country_iso3,
    d.country_name,
    d.region,
    d.income_group,
    c.year,
    -- emissions
    c.co2_mt,
    c.co2_per_capita,
    c.co2_per_gdp,
    -- energy
    e.primary_energy_twh,
    e.renewables_share_pct,
    e.fossil_share_pct,
    -- economic / social (World Bank WDI)
    w.gdp_per_capita_usd,
    w.gdp_usd,
    w.life_expectancy,
    w.population,
    w.poverty_rate,
    w.internet_users_pct,
    w.urban_pop_pct,
    w.forest_area_pct,
    w.renew_elec_pct,
    w.energy_imports_pct,
    -- EU household electricity price, EUR/kWh (Eurostat; null outside the EU/EEA)
    p.electricity_price_eur_kwh
from co2 as c
left join energy as e on c.country_iso3 = e.country_iso3 and c.year = e.year
left join wdi as w on c.country_iso3 = w.country_iso3 and c.year = w.year
left join eu_prices as p on c.country_iso3 = p.country_iso3 and c.year = p.year
left join country as d on c.country_iso3 = d.country_iso3
