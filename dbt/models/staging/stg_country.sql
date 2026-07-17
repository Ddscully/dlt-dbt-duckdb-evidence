-- Country dimension from the World Bank /country endpoint.
-- dlt flattens nested JSON: region__value, incomeLevel__value, etc.
with source as (
    select * from {{ source('raw', 'wb_country') }}
)

select
    id                           as country_iso3,
    iso2_code                    as country_iso2,
    name                         as country_name,
    region__value                as region,
    income_level__value          as income_group,
    capital_city                 as capital_city,
    cast(longitude as double)    as longitude,
    cast(latitude as double)     as latitude
from source
where region__value <> 'Aggregates'   -- keep only real countries
