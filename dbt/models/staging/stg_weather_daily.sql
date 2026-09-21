-- Daily capital-city ERA5 weather, turned into degree days. Its caveats — a
-- capital as a country's proxy, the preliminary recent tail, two degree-day
-- conventions — are the description in _staging.yml and the weather-models skill.
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
        -- The `case` because DuckDB's `greatest` ignores nulls, so a day with no
        -- temperature would score zero demand; null fails `not_null` instead.
        case
            when temp_mean_c is null then null
            else greatest({{ var('heating_degree_day_base_c') }} - temp_mean_c, 0)
        end as hdd_c,
        -- Both operands: half a known pair is an unknown degree day.
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

-- `country_iso3` landed at ingest; this join only locates the capital.
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

    -- Degree days, with their bases
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

    -- Location
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
