-- Emissions fact, cleaned to (country iso, year) grain.
with source as (
    select * from {{ source('raw', 'owid_co2') }}
)

select
    {{ owid_country_iso3() }} as country_iso3,
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
    -- the stock, not the flow: everything emitted since the country's first
    -- year of data, which is 1750 at the earliest
    cumulative_co2,
    share_global_cumulative_co2
from source
-- Drops the aggregates ("World", "Europe") and keeps Kosovo, which OWID also
-- leaves uncoded: see the macro.
where
    length({{ owid_country_iso3() }}) = 3
    -- OWID writes 0.0 for Kosovo's CO2 in 1994-1999, years whose emissions it
    -- counts inside Serbia's: a placeholder, not a measurement. A country of two
    -- million people does not emit nothing, and a zero passes every range test.
    -- Dropped rather than nulled, so the snapshot sees a withdrawn row and not
    -- a revision. Its unmeasured years before 1994 are null, as every country's
    -- are, and stay.
    and not (country = 'Kosovo' and co2 is not distinct from 0)
