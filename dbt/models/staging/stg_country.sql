-- Country dimension from the World Bank /country endpoint, plus manual
-- overrides for ISO3 codes the World Bank doesn't list (see country_overrides).
-- dlt flattens nested JSON: region__value, incomeLevel__value, etc.
with source as (
    select * from {{ source('raw', 'wb_country') }}
),

country_overrides as (
    select * from {{ ref('country_overrides') }}
),

world_bank as (
    select
        id as country_iso3,
        iso2_code as country_iso2,
        name as country_name,
        -- the API pads two of the region names ('Sub-Saharan Africa ')
        trim(region__value) as region,
        income_level__value as income_group,
        capital_city,
        -- try_cast: the API sends '' for territories with no coordinates
        try_cast(longitude as double) as longitude,
        try_cast(latitude as double) as latitude
    from source
    where region__value <> 'Aggregates'   -- keep only real countries
),

manual as (
    select
        o.country_iso3,
        o.country_iso2,
        o.country_name,
        o.region,
        o.income_group,
        cast(null as varchar) as capital_city,
        cast(null as double) as longitude,
        cast(null as double) as latitude
    from country_overrides as o
    -- the World Bank wins if it ever starts publishing one of these
    -- (the `is not null` guards NOT IN's three-valued logic: a single null
    -- country_iso3 in world_bank would otherwise make the predicate UNKNOWN
    -- for every row and silently drop all overrides)
    where o.country_iso3 not in (
        select w.country_iso3
        from world_bank as w
        where w.country_iso3 is not null
    )
)

select * from world_bank
union all
select * from manual
