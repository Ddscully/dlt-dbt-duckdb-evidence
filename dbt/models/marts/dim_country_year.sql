-- Country-year spine: every country in the dimension × every year the warehouse
-- covers. Facts join onto this instead of onto each other, so a (country, year)
-- only one source reports still reaches the mart, and a country-year no source
-- reports is a queryable gap rather than an absence.
-- Grain: one row per (country_iso3, year).
with country as (
    select * from {{ ref('stg_country') }}
),

co2 as (
    select * from {{ ref('stg_co2') }}
),

energy as (
    select * from {{ ref('stg_energy') }}
),

wdi as (
    select * from {{ ref('stg_wdi') }}
),

eu_prices as (
    select * from {{ ref('stg_eu_electricity_prices') }}
),

-- The span is taken from the data rather than hardcoded: OWID CO2 reaches back
-- to 1750 and the World Bank publishes the current year, and both ends move.
bounds as (
    select
        min(year) as first_year,
        max(year) as last_year
    from (
        select year from co2
        union all
        select year from energy
        union all
        select year from wdi
        union all
        select year from eu_prices
    ) as observed_years
    where year is not null
),

-- range()'s upper bound is exclusive
years as (
    select unnest(range(first_year, last_year + 1)) as year
    from bounds
)

select
    c.country_iso3,
    cast(y.year as integer) as year,
    c.country_name,
    c.region,
    c.income_group
from country as c
cross join years as y
