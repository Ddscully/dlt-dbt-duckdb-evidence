-- Version history of OWID's CO2 estimates, off the dbt snapshot. One row per
-- (country_iso3, year); `is_revised` marks the ones that changed since we first
-- loaded them. Deliberately unfiltered: Evidence can't write a zero-row source
-- to parquet, and on a freshly built warehouse nothing is revised yet.
--
-- Columns are named, not `select *`: the result ships to every visitor's
-- browser, so it carries what the pages read and a new mart column does not
-- join it unasked. A page that needs another column adds it here first.
select
    country_iso3,
    country_name,
    region,
    year,
    is_revised,
    version_count,
    first_loaded_at,
    last_revised_at,
    first_co2_mt,
    latest_co2_mt,
    co2_mt_change,
    co2_mt_change_pct
from marts.fct_co2_estimate_versions
