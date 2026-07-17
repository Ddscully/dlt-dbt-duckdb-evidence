-- Emissions fact, cleaned to (country iso, year) grain.
with source as (
    select * from {{ source('raw', 'owid_co2') }}
)

select
    iso_code                     as country_iso3,
    country,
    cast(year as integer)        as year,
    co2                          as co2_mt,
    co2_per_capita,
    co2_per_gdp,
    share_global_co2,
    coal_co2,
    oil_co2,
    gas_co2
from source
where iso_code is not null       -- drop aggregates (e.g. "World", "Europe")
  and length(iso_code) = 3
