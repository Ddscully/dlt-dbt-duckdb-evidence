-- The grid emission factor per (country_iso3, year), location-based and
-- lifecycle, with the vintage and lineage columns that make it a reference
-- table rather than a chart. Unfiltered: `is_latest_available` is the reporting
-- cross-section and the rest of the series is what the vintage section reads.
--
-- Columns are named, not `select *`: the result ships to every visitor's
-- browser, so it carries what the pages read and a new mart column does not
-- join it unasked. A page that needs another column adds it here first.
select
    country_iso3,
    country_name,
    region,
    year,
    emission_factor_g_co2_per_kwh,
    emission_factor_t_co2_per_mwh,
    electricity_generation_twh,
    low_carbon_share_elec_pct,
    is_latest_available,
    latest_available_year,
    latest_factor_lag_years,
    factor_version_count,
    first_published_factor_g_co2_per_kwh,
    is_restated,
    last_revised_at
from marts.dim_grid_emission_factors
