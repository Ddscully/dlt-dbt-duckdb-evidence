-- The grid emission factor per (country_iso3, year), location-based and
-- lifecycle, with the vintage and lineage columns that make it a reference
-- table rather than a chart. Unfiltered: `is_latest_available` is the reporting
-- cross-section and the rest of the series is what the vintage section reads.
select * from marts.dim_grid_emission_factors
