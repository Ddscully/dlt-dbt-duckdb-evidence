-- Capital-city weather aggregated to the country-year, so the daily archive can
-- join the rest of the warehouse. Grain: one row per (country_iso3, year).
--
-- **This exists to be a control variable.** Every other fact here answers "what
-- happened"; this one answers "was it colder that year", which is the question
-- you have to dispose of before an energy or price movement can be attributed to
-- anything else. The measured example: across six large EU markets, 2022 was
-- milder than 2021 in every one and inside a narrow band, while household
-- electricity prices over the same pair moved more than eighty points in both
-- directions — so weather explains essentially none of that divergence, and the
-- tax and gas-exposure story is what is left. That is a *negative* result, and
-- producing one at all is what a weather series buys.
--
-- Two things to read carefully before using a number from here:
--
-- 1. **Coverage is EU/EEA only**, because the scope was chosen to match
--    `fct_eu_electricity_prices_semiannual` exactly — same 41 countries. Joining
--    this to `fct_emissions_energy` leaves the rest of the world null, the same
--    way the electricity price column already does.
-- 2. **`year_is_complete` is not a formality.** The archive stops a few days
--    short of today, so the current year is always partial, and an annual
--    degree-day total over a partial year is not comparable with a whole one —
--    it is not even wrong in a stable direction, since the missing days are
--    whichever season the run happens to sit in. Filter on it for any
--    year-over-year comparison.
with daily as (
    select * from {{ ref('stg_weather_daily') }}
),

by_year as (
    select
        country_iso3,
        cast(date_part('year', weather_date) as integer) as year,

        count(*) as n_days,
        -- The number that decides whether the row is comparable. A leap year has
        -- 366, so this is compared against the calendar rather than against 365.
        max(weather_date) as last_day,

        -- Degree days: sums, because that is what a degree day *is* — a daily
        -- quantity whose annual total is the demand proxy. Both conventions are
        -- carried up from staging so a downstream reader can see they differ.
        sum(hdd_c) as hdd_total,
        sum(hdd_minmax_c) as hdd_minmax_total,
        sum(cdd_c) as cdd_total,
        max(heating_base_c) as heating_base_c,
        max(cooling_base_c) as cooling_base_c,

        -- Temperature: a mean of daily means, which is not the same as a mean of
        -- hourly values and is what ERA5's daily aggregate makes available. The
        -- extremes are the daily extremes, not the annual instantaneous ones.
        round(avg(temp_mean_c), 2) as temp_mean_c,
        min(temp_min_c) as temp_min_c,
        max(temp_max_c) as temp_max_c,
        count(*) filter (where temp_min_c < 0) as frost_days,

        -- The other three, at the aggregate each one is meaningful in: rain and
        -- sunlight accumulate over a year, wind does not.
        round(sum(precipitation_mm), 1) as precipitation_mm,
        round(sum(solar_radiation_mj_m2), 1) as solar_radiation_mj_m2,
        round(avg(wind_speed_max_kmh), 2) as wind_speed_max_kmh,

        -- Carried so the approximation stays visible at this grain too — see
        -- `stg_weather_daily`. Constant within a country, hence `max`.
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

    -- Completeness. Compared against the year's own length rather than 365, so a
    -- leap year is not permanently one day short of complete.
    n_days,
    last_day,
    n_days = date_part('dayofyear', make_date(year, 12, 31)) as year_is_complete,
    grid_distance_km
from by_year
