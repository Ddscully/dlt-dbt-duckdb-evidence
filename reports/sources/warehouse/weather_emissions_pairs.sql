-- Year-over-year change in heating demand beside year-over-year change in
-- territorial CO₂, one row per country and complete weather year.
--
-- The emissions half of the weather page. `weather_price_pairs.sql` asks
-- whether a colder year moved the household electricity price; this asks
-- whether it moved what the country burned, which is the attribution question
-- the page exists for.
--
-- **Unfiltered on purpose.** Every complete weather year is a row, with the
-- changes null where the previous year is missing or not adjacent, because an
-- empty source fails the build and this one would be empty on a cold-started
-- archive: OWID's CO₂ runs a year behind the weather, so the newest complete
-- weather year has no emissions to pair with. The pages filter on
-- `co2_change is not null`.
--
-- `previous_*` travel alongside the changes so a page can aggregate a panel's
-- total change (sum over sum) rather than averaging country percentages.
with weather as (
    select
        country_iso3,
        year,
        hdd_total
    from marts.fct_country_weather_year
    where year_is_complete
),

emissions as (
    select
        country_iso3,
        country_name,
        year,
        co2_mt,
        gas_co2
    from marts.fct_emissions_energy
),

paired as (
    select
        w.country_iso3,
        e.country_name,
        w.year,
        w.hdd_total,
        e.co2_mt,
        e.gas_co2,
        lag(w.year) over by_country as previous_year,
        lag(w.hdd_total) over by_country as previous_hdd_total,
        lag(e.co2_mt) over by_country as previous_co2_mt,
        lag(e.gas_co2) over by_country as previous_gas_co2
    from weather as w
    left join emissions as e
        on w.country_iso3 = e.country_iso3 and w.year = e.year
    window by_country as (partition by w.country_iso3 order by w.year)
),

adjacent as (
    select
        *,
        coalesce(year - previous_year = 1, false) as is_adjacent
    from paired
)

select
    country_iso3,
    country_name,
    year,
    co2_mt,
    case when is_adjacent then previous_co2_mt end as previous_co2_mt,
    case when is_adjacent then 100.0 * (hdd_total / previous_hdd_total - 1) end as hdd_change,
    case when is_adjacent then 100.0 * (co2_mt / previous_co2_mt - 1) end as co2_change,
    -- Below a megatonne a country's gas line is a handful of plants, and its
    -- percentage change is noise on a small base.
    case
        when is_adjacent and previous_gas_co2 >= 1
            then 100.0 * (gas_co2 / previous_gas_co2 - 1)
    end as gas_co2_change,
    -- 2020 was a mild winter and a lockdown, and 2021 a cold one and a rebound:
    -- both line up with the weather by coincidence, so every weather-emissions
    -- figure on the page leaves them out and says so.
    year in (2020, 2021) as is_pandemic_year
from adjacent
