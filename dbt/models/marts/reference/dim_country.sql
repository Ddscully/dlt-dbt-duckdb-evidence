-- The country dimension: one row per country, and the only place in this
-- warehouse that answers "what is a country".
-- Grain: one row per country_iso3.
--
-- The conformed country dimension every domain joins to: contracted, `public`,
-- and shipped as its own Parquet file, where `stg_country` is the `reference`
-- group's working copy of one API response. Not the spine: `dim_country_year`
-- crosses this with every year (what could have been reported).
--
-- The facts that also carry `country_name`, `region` and `income_group` inline
-- keep them: removing them from `fct_emissions_energy` saves 0.4% of its Parquet
-- (zstd dictionary-encodes repeated strings), and the copies cannot drift, being
-- built from this dimension in the same run.
with country as (
    select * from {{ ref('stg_country') }}
)

select
    country_iso3,
    country_iso2,
    country_name,
    region,
    income_group,
    capital_city,
    latitude,
    longitude,
    -- The 11 rows from the `country_overrides` seed; 10 have no income group
    -- (the seed fills Taiwan's), which is why that column is nullable.
    is_manual_entry
from country
