-- Eurostat household electricity prices, cleaned to the (country iso3, year) grain.
-- Source is semi-annual (2023-S1, 2023-S2) and keyed by Eurostat's 2-letter geo
-- code, which is ISO2 apart from EL=Greece (ISO2 GR) and UK=UK (ISO2 GB). We map
-- geo -> ISO2, join the country dim for ISO3, and average the two half-years.
with source as (
    select * from {{ source('raw', 'eu_elec_prices') }}
),

mapped as (
    select
        case geo
            when 'EL' then 'GR'   -- Eurostat uses EL for Greece
            when 'UK' then 'GB'   -- ... and UK for the United Kingdom
            else geo
        end as country_iso2,
        year,
        price_eur_kwh
    from source
    -- drop Eurostat aggregates (EU27_2020, EA); real geos are 2-letter codes
    where length(geo) = 2
),

country as (
    select
        country_iso2,
        country_iso3
    from {{ ref('stg_country') }}
)

select
    c.country_iso3,
    m.year,
    avg(m.price_eur_kwh) as electricity_price_eur_kwh
from mapped as m
inner join country as c on m.country_iso2 = c.country_iso2
group by c.country_iso3, m.year
