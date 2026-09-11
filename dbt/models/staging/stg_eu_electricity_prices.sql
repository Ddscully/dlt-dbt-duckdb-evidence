-- Eurostat household electricity prices averaged to the project's annual grain.
-- Grain: one row per (country_iso3, year).
--
-- Cleaning lives in `stg_eu_electricity_prices_semiannual`; this only
-- aggregates. Eurostat publishes S1 around May and S2 the next spring, so the
-- newest year usually holds one half: `n_half_years` says so, and the mart
-- carries it as `price_is_partial_year`.
with semiannual as (
    select * from {{ ref('stg_eu_electricity_prices_semiannual') }}
)

select
    country_iso3,
    year,
    avg(electricity_price_eur_kwh) as electricity_price_eur_kwh,
    count(*) as n_half_years
from semiannual
group by country_iso3, year
