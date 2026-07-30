-- Eurostat household electricity prices averaged to the project's annual grain.
-- Grain: one row per (country_iso3, year).
--
-- Cleaning lives in `stg_eu_electricity_prices_semiannual`; this model only
-- aggregates. `n_half_years` is the honesty column: Eurostat publishes S1 around
-- May and S2 the following spring, so the newest year here routinely holds one
-- half, and its "annual average" is a half-year wearing an annual label until S2
-- lands. Anything comparing years should check it — the mart carries it forward
-- as `price_is_partial_year`.
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
