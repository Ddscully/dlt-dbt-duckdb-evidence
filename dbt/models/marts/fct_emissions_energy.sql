-- Wide country-year fact: emissions + energy + WDI + EU prices on the spine.
-- Grain: one row per (country_iso3, year).
with spine as (
    select * from {{ ref('dim_country_year') }}
),

co2 as (
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

-- The country-years at least one source actually reports. The spine is a full
-- cross join, so without this the fact would carry an all-null row for
-- (Kosovo, 1750) and ~20k of its friends; `dim_country_year` is where you go
-- looking for those gaps.
observed as (
    select
        country_iso3,
        year
    from co2
    union
    select
        country_iso3,
        year
    from energy
    union
    select
        country_iso3,
        year
    from wdi
    union
    select
        country_iso3,
        year
    from eu_prices
)

select
    s.country_iso3,
    s.country_name,
    s.region,
    s.income_group,
    s.year,
    -- emissions, territorial (who burned it)
    c.co2_mt,
    c.co2_per_capita,
    c.co2_per_gdp,
    c.share_global_co2,
    c.coal_co2,
    c.oil_co2,
    c.gas_co2,
    -- emissions, consumption-based (who bought it). ~120 countries, ends a year
    -- before the territorial series — null is the normal case, not a defect.
    c.consumption_co2,
    c.consumption_co2_per_capita,
    c.trade_co2,
    c.trade_co2_share,
    -- cumulative: the stock since 1750, which is a different ranking to the flow
    c.cumulative_co2,
    c.share_global_cumulative_co2,
    -- energy
    e.primary_energy_twh,
    e.renewables_share_pct,
    e.fossil_share_pct,
    -- electricity (wider country coverage than the primary-energy columns)
    e.electricity_generation_twh,
    e.carbon_intensity_elec_g_kwh,
    e.low_carbon_share_elec_pct,
    e.solar_share_elec_pct,
    e.wind_share_elec_pct,
    e.nuclear_share_elec_pct,
    e.coal_share_elec_pct,
    e.gas_share_elec_pct,
    -- economic / social (World Bank WDI)
    w.gdp_per_capita_usd,
    w.gdp_usd,
    w.gdp_constant_usd,
    w.life_expectancy,
    w.population,
    w.poverty_rate,
    w.internet_users_pct,
    w.urban_pop_pct,
    w.forest_area_pct,
    w.renew_elec_pct,
    w.energy_imports_pct,
    -- EU household electricity price, EUR/kWh (Eurostat; null outside the EU/EEA).
    -- Averaged over the year's half-years, so it carries the flag saying when that
    -- average is over one of them — see fct_eu_electricity_prices_semiannual for
    -- the unaveraged series.
    p.electricity_price_eur_kwh,
    p.n_half_years < 2 as price_is_partial_year
from spine as s
inner join observed as o on s.country_iso3 = o.country_iso3 and s.year = o.year
left join co2 as c on s.country_iso3 = c.country_iso3 and s.year = c.year
left join energy as e on s.country_iso3 = e.country_iso3 and s.year = e.year
left join wdi as w on s.country_iso3 = w.country_iso3 and s.year = w.year
left join eu_prices as p on s.country_iso3 = p.country_iso3 and s.year = p.year
