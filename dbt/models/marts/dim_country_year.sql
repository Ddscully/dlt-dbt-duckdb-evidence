-- Country-year spine: every country in the dimension × every year the warehouse
-- covers. Facts join onto this instead of onto each other, so a (country, year)
-- only one source reports still reaches the mart, and a country-year no source
-- reports is a queryable gap rather than an absence.
-- Grain: one row per (country_iso3, year).
with country as (
    select * from {{ ref('stg_country') }}
),

-- The span is taken from the data rather than hardcoded: OWID CO2 reaches back
-- to 1750 and the World Bank publishes the current year, and both ends move.
--
-- Which sources "the data" means is `int_country_year_observed`, and it is a
-- ref rather than a union here because `fct_emissions_energy` needs the same
-- answer. The two used to list the four staging models separately; see that
-- model's header for what drifting apart cost.
bounds as (
    select
        min(year) as first_year,
        max(year) as last_year
    from {{ ref('int_country_year_observed') }}
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
