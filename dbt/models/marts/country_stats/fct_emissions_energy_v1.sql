{{ config(materialized='view') }}

-- v1 of the wide country-year fact, kept live so the rename in v2 is a migration
-- rather than a breakage. It is a view over v2 that puts one column back under
-- its old name — there is no second copy of the logic, and no second copy of the
-- 43k rows.
--
-- What changed, and why it was worth a version: v2 renames `co2_per_gdp` to
-- `co2_kg_per_gdp_ppp_2011`. The old name says neither the unit nor the basis,
-- and the warehouse has a second carbon-intensity column with a *different*
-- basis — `analytics.co2_intensity.co2_per_gdp_const_usd`, kg CO2 per constant
-- 2015 US$, ~197 countries through 2024 against this one's 164 through 2022 in
-- 2011 international-$ (PPP). The two are not comparable in levels and the old
-- names gave a reader no way to know that.
--
-- The rename is invisible inside this project (nothing refs this mart) and
-- breaking outside it: the monthly data release ships the mart as a Parquet file
-- to consumers who cannot be paged. That asymmetry is the whole reason for
-- `versions:` — `deprecation_date` in `_country_stats.yml` is the promise on it.
--
-- `select * exclude … , … as co2_per_gdp` puts the renamed column last rather
-- than in its original position, which is deliberate: the contract for v1 is
-- declared the same way (`include: all`, `exclude:` the new name, then the old
-- one appended), so the yml and the SQL agree on the order dbt enforces.
select
    * exclude (co2_kg_per_gdp_ppp_2011),
    co2_kg_per_gdp_ppp_2011 as co2_per_gdp
from {{ ref('fct_emissions_energy', v=2) }}
