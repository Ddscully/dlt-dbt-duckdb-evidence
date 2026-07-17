-- Energy fact, cleaned to (country iso, year) grain.
with source as (
    select * from {{ source('raw', 'owid_energy') }}
)

select
    iso_code                     as country_iso3,
    country,
    cast(year as integer)        as year,
    primary_energy_consumption   as primary_energy_twh,
    renewables_share_energy      as renewables_share_pct,
    fossil_share_energy          as fossil_share_pct,
    energy_per_capita,
    energy_per_gdp
from source
where iso_code is not null
  and length(iso_code) = 3
