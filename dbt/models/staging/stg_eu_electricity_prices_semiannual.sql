-- Eurostat household electricity prices at the grain Eurostat publishes them,
-- semi-annual: the cleaning model, where geo -> ISO3 happens once.
-- `stg_eu_electricity_prices` averages it to the annual grain.
with source as (
    select * from {{ source('raw', 'eu_elec_prices') }}
),

mapped as (
    select
        period,
        case geo
            when 'EL' then 'GR'   -- Eurostat uses EL for Greece
            when 'UK' then 'GB'   -- ... and UK for the United Kingdom
            else geo
        end as country_iso2,
        -- The composition of Eurostat's `EU27_2020` aggregate, in Eurostat's own
        -- codes: today's members at every half-year, so Croatia counts before
        -- 2013 and the United Kingdom never does.
        geo in (
            'AT', 'BE', 'BG', 'CY', 'CZ', 'DE', 'DK', 'EE', 'EL',
            'ES', 'FI', 'FR', 'HR', 'HU', 'IE', 'IT', 'LT', 'LU',
            'LV', 'MT', 'NL', 'PL', 'PT', 'RO', 'SE', 'SI', 'SK'
        ) as is_eu_member,
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

-- The one aggregate kept, as a column beside each country's price and not as a
-- row: it is no country, so the grain has no place for it. Eurostat weights it
-- by each member's household consumption, which these rows cannot reproduce.
eu27 as (
    select
        period,
        price_eur_kwh as eu27_price_eur_kwh
    from source
    where geo = 'EU27_2020'
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
    m.price_eur_kwh as electricity_price_eur_kwh,
    m.is_eu_member,
    e.eu27_price_eur_kwh
from mapped as m
inner join country as c on m.country_iso2 = c.country_iso2
left join eu27 as e on m.period = e.period
