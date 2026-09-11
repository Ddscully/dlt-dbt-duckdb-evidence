-- Capital-city weather aggregated to the country-year, for the 41 EU/EEA
-- countries the electricity-price mart also covers.
--
-- The mart is keyed on ISO3 only, so the dimension supplies the labels (both
-- models are in `evidence_weather`'s `depends_on`).
--
-- Unfiltered: the page applies `year_is_complete` per block because it also
-- explains the partial current year, and a filtered source can come back empty,
-- which fails the build.
select
    w.country_iso3,
    dim.country_name,
    dim.region,
    w.year,

    -- Degree days, in both conventions, with the base each was computed against.
    w.hdd_total,
    w.hdd_minmax_total,
    w.cdd_total,
    w.heating_base_c,
    w.cooling_base_c,

    -- Temperature
    w.temp_mean_c,
    w.temp_min_c,
    w.temp_max_c,
    w.frost_days,

    -- Other measurements
    w.precipitation_mm,
    w.solar_radiation_mj_m2,
    w.wind_speed_max_kmh,

    -- Completeness and the size of the spatial approximation.
    w.n_days,
    w.last_day,
    w.year_is_complete,
    w.grid_distance_km
from marts.fct_country_weather_year as w
left join (
    select distinct
        country_iso3,
        country_name,
        region
    from marts.dim_country_year
) as dim on w.country_iso3 = dim.country_iso3
