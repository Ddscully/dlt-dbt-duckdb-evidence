-- Year-over-year change in heating demand beside year-over-year change in
-- household electricity price, one row per country and year.
--
-- A source query rather than a page block: Evidence prerendering does not
-- resolve a query that reads another query (the page builds with `Table with
-- name pairs does not exist` inside a 200), and the windows run once at build
-- time instead of in every browser.
--
-- 1. **Both halves of the price year.** Eurostat publishes S1 around May and S2
--    the next spring, so `having count(*) = 2` drops a half-reported year.
-- 2. **`year_is_complete`** on the weather side, for the same reason.
-- 3. **Adjacent years only.** Neither series is guaranteed contiguous, so both
--    gaps must be exactly one year; a `lag` over a hole would compare 2012 with
--    2007.
--
-- **It can come back empty**, and an empty source fails the build (`too small to
-- be a Parquet file`). It needs two adjacent complete years in both series; a
-- cold-started weather archive (`WEATHER_COLD_START_YEARS`, three years, so two
-- complete ones) just meets that. If it fails to write, the weather archive is
-- too shallow.
with weather as (
    select
        country_iso3,
        year,
        hdd_total
    from marts.fct_country_weather_year
    where year_is_complete
),

prices as (
    select
        country_iso3,
        max(country_name) as country_name,
        year,
        avg(electricity_price_eur_kwh) as price_eur_kwh
    from marts.fct_eu_electricity_prices_semiannual
    group by country_iso3, year
    having count(*) = 2
),

changes as (
    select
        w.country_iso3,
        p.country_name,
        w.year,
        100.0 * (w.hdd_total / lag(w.hdd_total) over w_win - 1) as hdd_change,
        100.0 * (p.price_eur_kwh / lag(p.price_eur_kwh) over p_win - 1) as price_change,
        w.year - lag(w.year) over w_win as weather_gap,
        p.year - lag(p.year) over p_win as price_gap
    from weather as w
    inner join prices as p
        on w.country_iso3 = p.country_iso3 and w.year = p.year
    window
        w_win as (partition by w.country_iso3 order by w.year),
        p_win as (partition by p.country_iso3 order by p.year)
),

adjacent as (
    select
        country_iso3,
        country_name,
        year,
        hdd_change,
        price_change
    from changes
    where weather_gap = 1
        and price_gap = 1
        and hdd_change is not null
        and price_change is not null
),

-- How far apart the countries were each year; a separate pass because the flag
-- below is a window over these windows.
spreads as (
    select
        country_iso3,
        country_name,
        year,
        hdd_change,
        price_change,
        max(price_change) over year_win - min(price_change) over year_win as price_spread,
        max(hdd_change) over year_win - min(hdd_change) over year_win as hdd_spread
    from adjacent
    window year_win as (partition by year)
)

select
    country_iso3,
    country_name,
    year,
    hdd_change,
    price_change,
    price_spread,
    hdd_spread,

    -- So the page can name the year prices diverged most without hardcoding it.
    price_spread = max(price_spread) over () as is_widest_spread_year
from spreads
