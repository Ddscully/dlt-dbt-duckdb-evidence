-- The country dimension: one row per country, and the only place in this
-- warehouse that answers "what is a country".
-- Grain: one row per country_iso3.
--
-- **It exists because `stg_country` was doing a mart's job.** Five models across
-- three groups read the cleaning model directly — which is why it carries an
-- `access: protected` override — and the monthly release published the country
-- attributes only as columns repeated inside facts, so a consumer wanting the
-- 228 countries had to deduplicate a 62,928-row spine to find them. A conformed
-- dimension that every domain joins to is exactly what `_groups.yml` says the
-- `reference` group is for, and until now that description named a model that
-- did not exist.
--
-- Two things it deliberately is not:
--
-- 1. **Not a rename of `stg_country`.** It is the *published* dimension —
--    contracted, `public`, and shipped as its own Parquet file — where the
--    staging model is `reference`'s working copy of one API response. The
--    difference matters at the group boundary: `fct_cbam_exposure` reads this
--    now instead of reaching into another group's cleaning layer.
-- 2. **Not the spine.** `dim_country_year` is this crossed with every year the
--    data covers, which is a different question (what could have been reported)
--    from this one (what is a country).
--
-- The three facts that carry `country_name`, `region` and `income_group` inline
-- keep them, and that is measured rather than conceded: stripping them from
-- `fct_emissions_energy` saves 6.7 kB of a 1,591 kB Parquet — 0.4%, because
-- zstd dictionary-encodes 228 repeated strings to almost nothing — and the
-- copies cannot drift, since every one is built from this dimension in the same
-- run. What a single-file consumer would lose is real; what they would gain is
-- 0.4%.
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
    -- From the `country_overrides` seed rather than the World Bank endpoint.
    -- 11 rows, 10 of which is why `income_group` is nullable here — the seed
    -- fills Taiwan's.
    is_manual_entry
from country
