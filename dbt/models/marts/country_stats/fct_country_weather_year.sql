-- Capital-city weather aggregated to the country-year, so the daily archive can
-- join the rest of the warehouse. Grain: one row per (country_iso3, year).
--
-- **A control variable**: "was it colder that year" has to be answered before an
-- energy or price movement can be attributed to anything else (the
-- `weather-models` skill has the worked example).
--
-- 1. **EU/EEA only** — the 41 countries of
--    `fct_eu_electricity_prices_semiannual`. Joined to `fct_emissions_energy`,
--    the rest of the world is null.
-- 2. **Filter on `year_is_complete` for any year-over-year comparison.** The
--    archive stops a few days short of today, and a partial year's degree-day
--    total is not comparable — not even in a stable direction, since which
--    season is missing depends on the date.
with daily as (
    select * from {{ ref('stg_weather_daily') }}
),

by_year as (
    select
        country_iso3,
        cast(date_part('year', weather_date) as integer) as year,

        count(*) as n_days,
        max(weather_date) as last_day,

        -- Summed: a degree day is a daily quantity whose annual total is the
        -- demand proxy. Both conventions carry through.
        sum(hdd_c) as hdd_total,
        sum(hdd_minmax_c) as hdd_minmax_total,
        sum(cdd_c) as cdd_total,
        max(heating_base_c) as heating_base_c,
        max(cooling_base_c) as cooling_base_c,

        -- A mean of ERA5's daily means (not of hourly values); the extremes are
        -- of daily extremes.
        round(avg(temp_mean_c), 2) as temp_mean_c,
        min(temp_min_c) as temp_min_c,
        max(temp_max_c) as temp_max_c,
        count(*) filter (where temp_min_c < 0) as frost_days,

        -- Rain and sunlight accumulate over a year; wind is averaged.
        round(sum(precipitation_mm), 1) as precipitation_mm,
        round(sum(solar_radiation_mj_m2), 1) as solar_radiation_mj_m2,
        round(avg(wind_speed_max_kmh), 2) as wind_speed_max_kmh,

        -- Constant within a country (see `stg_weather_daily`), hence `max`.
        max(grid_distance_km) as grid_distance_km
    from daily
    group by country_iso3, cast(date_part('year', weather_date) as integer)
)

select
    country_iso3,
    year,

    -- Degree days
    hdd_total,
    hdd_minmax_total,
    cdd_total,
    heating_base_c,
    cooling_base_c,

    -- Temperature
    temp_mean_c,
    temp_min_c,
    temp_max_c,
    frost_days,

    -- Other measurements
    precipitation_mm,
    solar_radiation_mj_m2,
    wind_speed_max_kmh,

    -- Against the year's own length, so a leap year can be complete.
    n_days,
    last_day,
    n_days = date_part('dayofyear', make_date(year, 12, 31)) as year_is_complete,
    grid_distance_km
from by_year
