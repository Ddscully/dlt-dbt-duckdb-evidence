-- Year-over-year change in heating demand beside year-over-year change in
-- household electricity price, one row per country and year.
--
-- This is a *source* query and not a block on the page for two reasons. Evidence
-- resolves source tables when it prerenders a page but does not resolve a query
-- that reads another query, so a chained version builds with `Table with name
-- pairs does not exist` in the middle of the page and 200 OK around it. And the
-- window functions below then run once at build time rather than in every
-- visitor's browser.
--
-- Three things this query is careful about:
--
-- 1. **Both halves, or the annual price is not an annual price.** Eurostat
--    publishes S1 around May and S2 the following spring, so `having count(*) =
--    2` drops the year that is only half reported. Without it the newest year is
--    a January-June average being compared against whole ones.
-- 2. **`year_is_complete`, for the same reason on the weather side.** The
--    archive stops a few days short of today, and a degree-day total over a
--    partial year is not comparable with a whole one.
-- 3. **A "year over year" change across a gap is not one.** Neither series is
--    guaranteed contiguous — the weather archive holds whichever years have been
--    fetched — so both gaps are computed and both must be exactly one year. A
--    `lag` over a hole silently compares 2012 against 2007.
--
-- **This is a filtered source, which is the shape that can come back empty**, and
-- an empty source is a build failure (`too small to be a Parquet file`) rather
-- than an empty chart. It needs two *adjacent* complete years present in both
-- series. A fresh clone or `pages.yml` cold-starts the weather archive at
-- `WEATHER_COLD_START_YEARS` (three years, so two complete ones), which clears
-- that bar by one year — so if this ever fails to write, the cause is the weather
-- archive being shallower than the overlap needs, not the SQL.
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

-- How far apart the countries were that year. Two passes rather than one,
-- because a window function cannot be nested inside another one and the flag
-- below is a max over these per-year spreads.
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

    -- Carried so the page can point at "the year prices diverged most" without
    -- three copies of the same argmax subquery, and so that which year that is
    -- stays a fact about the data rather than a literal somebody typed.
    price_spread = max(price_spread) over () as is_widest_spread_year
from spreads
