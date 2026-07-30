-- Eurostat household electricity prices at the grain Eurostat publishes them:
-- semi-annual. Grain: one row per (country_iso3, year, half).
--
-- This is the cleaning model — geo -> ISO2 -> ISO3 happens here, once, and
-- `stg_eu_electricity_prices` averages this to the project's annual grain.
-- Both exist because the average is lossy in a way the source isn't: within
-- 2022 the Netherlands went 0.034 -> 0.142 EUR/kWh as the energy-tax cuts landed
-- in the first half, and the annual mean of 0.088 is a price no household paid.
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
        -- '2023-S1' -> 'S1'. The period string is the only place the half lives.
        substr(period, 6, 2) as half,
        price_eur_kwh
    from source
    -- Drops the long aggregate codes (EU27_2020, EA19). Note that this does
    -- *not* drop 'EA' (euro area) — two letters, so it survives here and falls
    -- out at the inner join below, which no ISO2 code matches.
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
    m.half,
    m.year || '-' || m.half as period,
    -- A real date for the start of the half-year, so a chart can put this on a
    -- time axis instead of sorting 'S1'/'S2' strings.
    make_date(m.year, case m.half when 'S1' then 1 else 7 end, 1) as period_start_date,
    m.price_eur_kwh as electricity_price_eur_kwh
from mapped as m
inner join country as c on m.country_iso2 = c.country_iso2
