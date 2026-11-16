-- The wide country-year fact built by dbt (marts schema).
--
-- Columns are named, not `select *`: the result ships to every visitor's
-- browser, so it carries what the pages read and a new mart column does not
-- join it unasked. A page that needs another column adds it here first.
select
    country_iso3,
    country_name,
    region,
    income_group,
    year,
    co2_mt,
    co2_per_capita,
    share_global_co2,
    coal_co2,
    oil_co2,
    gas_co2,
    consumption_co2,
    cumulative_co2,
    share_global_cumulative_co2,
    primary_energy_twh,
    renewables_share_pct,
    electricity_generation_twh,
    carbon_intensity_elec_g_kwh,
    low_carbon_share_elec_pct,
    coal_share_elec_pct,
    gdp_per_capita_usd,
    gdp_usd,
    gdp_constant_usd,
    life_expectancy,
    population,
    poverty_rate,
    electricity_price_eur_kwh,
    price_is_partial_year
from marts.fct_emissions_energy
