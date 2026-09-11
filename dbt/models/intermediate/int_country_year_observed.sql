-- The country-years the country-stats sources actually report, one row each.
-- Grain: one row per (country_iso3, year).
--
-- The single list of this domain's sources. `dim_country_year` sizes its spine
-- from its `min`/`max` year, and `fct_emissions_energy` uses the pairs to cut
-- the spine's cross join back to reported country-years; a source missing from
-- either copy would silently lose rows, so there is one copy. A new
-- country-stats source is added here.
--
-- `year is not null` serves the spine's `min(year)`; a null key never matched
-- the fact anyway.
with co2 as (
    select
        country_iso3,
        year
    from {{ ref('stg_co2') }}
),

energy as (
    select
        country_iso3,
        year
    from {{ ref('stg_energy') }}
),

wdi as (
    select
        country_iso3,
        year
    from {{ ref('stg_wdi') }}
),

eu_prices as (
    select
        country_iso3,
        year
    from {{ ref('stg_eu_electricity_prices') }}
),

-- `union`, not `union all`: consumers want the set of country-years.
observed as (
    select * from co2
    union
    select * from energy
    union
    select * from wdi
    union
    select * from eu_prices
)

select
    country_iso3,
    cast(year as integer) as year
from observed
where year is not null
