-- Daily capital-city weather from Open-Meteo's ERA5 archive, cleaned and turned
-- into degree days. Grain: one row per (country_iso3, weather_date).
--
-- The spatial join happens at ingest: the resource requests each capital's
-- World Bank coordinates and lands `country_iso3` on every row. This model joins
-- `stg_country` only to measure how far the answering grid cell is from the
-- capital.
--
--
-- 1. **A capital is a coarse proxy for a country.** One ERA5 grid cell stands in
--    for national heating demand: defensible for comparing a country with itself
--    across years, weak for comparing countries. A population-weighted average
--    would cost many times the API budget; `grid_distance_km` shows the
--    approximation.
-- 2. **The recent tail is preliminary.** ERA5T is replaced by final ERA5 two to
--    three months later, and the ingest re-asks for the last 90 days, so rows in
--    that window can change between builds; older rows are carried forward, not
--    refetched.
-- 3. **Degree days are a convention, and this model ships two.** `hdd_c` uses
--    the day's mean temperature; `hdd_minmax_c` uses (max + min)/2, what a
--    max/min thermometer series reports. Neither is consistently the larger
--    (the `weather-models` skill has the measurement), which is why both ship.
with source as (
    select * from {{ source('raw', 'om_weather_daily') }}
),

renamed as (
    select
        country_iso3,
        weather_date,

        -- Renamed once here, with the unit in each name.
        temperature_2m_mean as temp_mean_c,
        temperature_2m_max as temp_max_c,
        temperature_2m_min as temp_min_c,
        precipitation_sum as precipitation_mm,
        wind_speed_10m_max as wind_speed_max_kmh,
        shortwave_radiation_sum as solar_radiation_mj_m2,

        -- The grid cell ERA5 answered from, not the capital (the API snaps to
        -- the nearest cell centre).
        grid_latitude,
        grid_longitude,
        elevation_m
    from source
),

daily as (
    select
        *,
        -- Clamped at zero: a day warmer than the base adds no heating demand. The
        -- `case` is required because DuckDB's `greatest` ignores nulls
        -- (`greatest(15.5 - null, 0)` is 0, measured on 1.5.5), so a day with no
        -- temperature would otherwise score zero demand and pass every test.
        -- Null instead fails this column's `not_null`, naming the day.
        case
            when temp_mean_c is null then null
            else greatest({{ var('heating_degree_day_base_c') }} - temp_mean_c, 0)
        end as hdd_c,
        -- Both operands, because the average of a known max and an unknown min is
        -- not a half-known degree day, it is an unknown one.
        case
            when temp_max_c is null or temp_min_c is null then null
            else greatest(
                {{ var('heating_degree_day_base_c') }} - (temp_max_c + temp_min_c) / 2, 0
            )
        end as hdd_minmax_c,
        case
            when temp_mean_c is null then null
            else greatest(temp_mean_c - {{ var('cooling_degree_day_base_c') }}, 0)
        end as cdd_c
    from renamed
),

located as (
    select
        d.*,
        c.latitude as capital_latitude,
        c.longitude as capital_longitude
    from daily as d
    inner join {{ ref('stg_country') }} as c
        on d.country_iso3 = c.country_iso3
)

select
    country_iso3,
    weather_date,

    -- Temperature
    temp_mean_c,
    temp_max_c,
    temp_min_c,

    -- Degree days, with the bases that produced them on every row: a total is
    -- meaningless without its base.
    hdd_c,
    hdd_minmax_c,
    cdd_c,
    -- Cast: a bare `15.5` is DECIMAL(3,1), whose precision the mart contract
    -- would have to declare and a different var value would change.
    cast({{ var('heating_degree_day_base_c') }} as double) as heating_base_c,
    cast({{ var('cooling_degree_day_base_c') }} as double) as cooling_base_c,

    -- Other measurements
    precipitation_mm,
    wind_speed_max_kmh,
    solar_radiation_mj_m2,

    -- Great-circle distance from the capital to the grid cell that answered.
    -- ERA5's 0.25-degree grid bounds it at about 20 km (less toward the poles);
    -- much more means the coordinates moved.
    grid_latitude,
    grid_longitude,
    elevation_m,
    round(
        6371 * acos(
            least(
                1.0,
                cos(radians(capital_latitude)) * cos(radians(grid_latitude))
                * cos(radians(grid_longitude) - radians(capital_longitude))
                + sin(radians(capital_latitude)) * sin(radians(grid_latitude))
            )
        ),
        1
    ) as grid_distance_km
from located
