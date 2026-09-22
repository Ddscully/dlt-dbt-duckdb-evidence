-- The conformed country dimension: one row per country, and the only place in
-- this warehouse that answers "what is a country". What it is and is not is the
-- description in _reference.yml.
with country as (
    select * from {{ ref('stg_country') }}
)

select
    country_iso3,
    country_iso2,
    country_name,
    region,
    income_group,
    capital_city,
    latitude,
    longitude,
    -- The 11 rows from the `country_overrides` seed; 10 have no income group
    -- (the seed fills Taiwan's), which is why that column is nullable.
    is_manual_entry
from country
