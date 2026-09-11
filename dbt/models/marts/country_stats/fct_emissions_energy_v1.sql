{{ config(materialized='view') }}

-- v1 of the wide country-year fact: a view over v2 that puts one column back
-- under its old name, so the rename is a migration rather than a breakage for
-- release consumers (nothing in the project refs it). `deprecation_date` in
-- `_country_stats.yml` is the removal promise.
--
-- v2 renamed `co2_per_gdp` to `co2_kg_per_gdp_ppp_2011`: the old name gave
-- neither unit nor basis, beside `analytics.co2_intensity.co2_per_gdp_const_usd`,
-- which has a different basis (constant 2015 US$) and is not comparable in level.
--
-- The old name goes last, matching the v1 contract (`include: all`, `exclude:`
-- the new name, the old one appended), so SQL and yml agree on column order.
select
    * exclude (co2_kg_per_gdp_ppp_2011),
    co2_kg_per_gdp_ppp_2011 as co2_per_gdp
from {{ ref('fct_emissions_energy', v=2) }}
