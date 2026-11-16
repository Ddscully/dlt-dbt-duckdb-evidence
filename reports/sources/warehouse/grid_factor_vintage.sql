-- One row per vintage: how many countries' latest grid factor is from that
-- year, and the share of countries a cut-off at that year would keep.
-- Cumulative *descending*, because "cut at year X" means "accept a factor from
-- X or later".
--
-- `vintage_year` is the category axis. Made here, server-side, it is a clean
-- '2024'; made on a page it needs a double cast, because the extract hands a
-- page every number as a double.
--
-- Read by the Scope 2 overview and its vintage page.
with by_year as (
    select latest_available_year, count(*) as n_countries
    from marts.dim_grid_emission_factors
    where is_latest_available
    group by latest_available_year
)

select
    latest_available_year                                   as factor_year,
    cast(latest_available_year as varchar)                  as vintage_year,
    n_countries                                             as countries,
    100.0 * sum(n_countries) over (order by latest_available_year desc)
        / sum(n_countries) over ()                          as coverage
from by_year
order by latest_available_year
