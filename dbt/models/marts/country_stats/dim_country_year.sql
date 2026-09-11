-- Country-year spine: every country in the dimension × every year the warehouse
-- covers. Facts join onto this instead of onto each other, so a (country, year)
-- only one source reports still reaches the mart, and a country-year no source
-- reports is a queryable gap rather than an absence.
-- Grain: one row per (country_iso3, year).
with country as (
    select * from {{ ref('dim_country') }}
),

-- The span comes from the data (OWID CO2 reaches back to 1750, the World Bank
-- to the current year), via the source list `fct_emissions_energy` shares.
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
