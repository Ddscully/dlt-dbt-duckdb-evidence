-- Carbon-efficiency ranking produced by the Polars transform (analytics schema).
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
    renewables_share_pct,
    carbon_intensity_elec_g_kwh,
    low_carbon_share_elec_pct,
    coal_share_elec_pct,
    gdp_per_capita_usd,
    life_expectancy,
    population,
    electricity_price_eur_kwh,
    price_is_partial_year,
    co2_per_gdp_const_usd
from analytics.co2_intensity
