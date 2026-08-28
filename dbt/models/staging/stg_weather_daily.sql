-- Daily capital-city weather from Open-Meteo's ERA5 archive, cleaned and turned
-- into degree days. Grain: one row per (country_iso3, weather_date).
--
-- **This is the warehouse's second daily grain and its first spatial join.**
-- Every other country model here keys on an ISO3 code somebody else assigned;
-- this one exists because `stg_country` carries the World Bank's capital-city
-- latitude and longitude, which nothing read until now. The ingest layer sends
-- those coordinates to Open-Meteo and lands what comes back, so the join is
-- already done by the time the rows arrive — `country_iso3` is on every row.
--
-- Three things worth knowing about it:
--
-- 1. **A capital is a proxy for a country, and a coarse one.** One ERA5 grid
--    cell stands in for national heating demand, which is defensible for
--    comparing a country with *itself* across years — the thing the degree-day
--    columns are for — and much weaker for comparing countries with each other.
--    Spain's population is not in Madrid's climate. A population-weighted
--    average over many cells is the honest version and costs many times the API
--    budget; `grid_distance_km` below is what makes the approximation visible
--    rather than implied.
-- 2. **The recent tail is preliminary.** Open-Meteo serves ERA5T within a day or
--    two of real time and Copernicus replaces it with final ERA5 two to three
--    months later. The ingest layer re-asks for the last 90 days on every run,
--    so rows inside that window can change value between builds; rows outside it
--    are frozen, because they are carried forward between releases rather than
--    refetched.
-- 3. **Degree days are a convention, and this model ships two of them.**
--    `hdd_c` uses the day's mean temperature; `hdd_minmax_c` uses (max + min)/2,
--    which is what a station-based series usually reports because it is all a
--    max/min thermometer records. They are not the same number — the second runs
--    warmer wherever a day is asymmetric — and having both is what makes the
--    choice testable instead of asserted.
with source as (
    select * from {{ source('raw', 'om_weather_daily') }}
),

renamed as (
    select
        country_iso3,
        weather_date,

        -- Kept under the API's own names in `raw`, renamed once here. Units are
        -- in the column name because a bare `temperature` is the shape of
        -- question this warehouse has already been bitten by — see the two
        -- carbon-intensity columns with different bases in `fct_emissions_energy`.
        temperature_2m_mean as temp_mean_c,
        temperature_2m_max as temp_max_c,
        temperature_2m_min as temp_min_c,
        precipitation_sum as precipitation_mm,
        wind_speed_10m_max as wind_speed_max_kmh,
        shortwave_radiation_sum as solar_radiation_mj_m2,

        -- The cell ERA5 actually answered from, which is not the capital: the
        -- API snaps a request to the nearest grid-cell centre and reports where
        -- it landed. Berlin's 52.5235/13.4115 comes back as 52.54833/13.407822.
        grid_latitude,
        grid_longitude,
        elevation_m
    from source
),

daily as (
    select
        *,
        -- The two conventions, both clamped at zero: a day warmer than the base
        -- contributes no heating demand, it does not contribute negative demand.
        -- `greatest(…, 0)` rather than a `case`, so a null temperature stays null
        -- instead of becoming a zero that sums silently into an annual total.
        greatest({{ var('heating_degree_day_base_c') }} - temp_mean_c, 0) as hdd_c,
        greatest(
            {{ var('heating_degree_day_base_c') }} - (temp_max_c + temp_min_c) / 2, 0
        ) as hdd_minmax_c,
        greatest(temp_mean_c - {{ var('cooling_degree_day_base_c') }}, 0) as cdd_c
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

    -- Degree days, with the policy that produced them carried alongside — the
    -- same reasoning as `dim_date.fiscal_year_start_month`. A degree-day total
    -- lifted out of this warehouse is meaningless without its base, and the
    -- alternative to carrying it is hoping the reader checks `dbt_project.yml`.
    hdd_c,
    hdd_minmax_c,
    cdd_c,
    -- Cast, because a bare `15.5` literal is DECIMAL(3,1) in DuckDB, not
    -- DOUBLE. The mart contract declares a type and would have to declare
    -- the decimal's precision with it — which would then move the moment
    -- somebody set the var to 18. A base temperature is a measurement.
    cast({{ var('heating_degree_day_base_c') }} as double) as heating_base_c,
    cast({{ var('cooling_degree_day_base_c') }} as double) as cooling_base_c,

    -- Other measurements
    precipitation_mm,
    wind_speed_max_kmh,
    solar_radiation_mj_m2,

    -- Provenance of the approximation. Great-circle distance from the capital
    -- the request named to the grid cell that answered it, so "a capital stands
    -- in for a country" carries a number rather than a caveat. ERA5's grid is
    -- 0.25 degrees, so this is bounded by about 20 km at the equator and less
    -- toward the poles; anything much larger means the coordinates moved.
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
