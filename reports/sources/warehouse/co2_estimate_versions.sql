-- Version history of OWID's CO2 estimates, off the dbt snapshot. One row per
-- (country_iso3, year); `is_revised` marks the ones that changed since we first
-- loaded them. Deliberately unfiltered: Evidence can't write a zero-row source
-- to parquet, and on a freshly built warehouse nothing is revised yet.
select * from marts.fct_co2_estimate_versions
