-- Emissions fact, cleaned to (country iso, year) grain.
with source as (
    select * from {{ source('raw', 'owid_co2') }}
)

select
    iso_code as country_iso3,
    country,
    cast(year as integer) as year,
    -- territorial (production-based) emissions
    co2 as co2_mt,
    co2_per_capita,
    co2_per_gdp,
    share_global_co2,
    -- what the tonnage is made of
    coal_co2,
    oil_co2,
    gas_co2,
    -- consumption-based: territorial emissions adjusted for the carbon embodied
    -- in trade. Only ~120 countries and it stops a year before the territorial
    -- series, so expect nulls where `co2_mt` has a number.
    consumption_co2,
    consumption_co2_per_capita,
    trade_co2,
    trade_co2_share,
    -- the stock, not the flow: everything emitted since 1750
    cumulative_co2,
    share_global_cumulative_co2
from source
where
    iso_code is not null       -- drop aggregates (e.g. "World", "Europe")
    and length(iso_code) = 3
