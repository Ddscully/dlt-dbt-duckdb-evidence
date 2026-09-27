---
title: Method and limits
description: The two degree-day conventions, what a capital's grid cell stands for, and the limits of the archive.
sidebar_position: 4
---

[← Weather](/weather)

**A degree-day total means nothing without its base and its convention, and one grid cell at a capital is a proxy for a country, not a measurement of it.**

## Two degree-day conventions, and they disagree

A degree day needs a daily temperature, and there are two answers to what that
is. One convention uses the day's mean; the other uses the midpoint of the day's
high and low, which is what a station-based series reports because it is all a
max/min thermometer can record. Neither is more correct, and this warehouse ships
both rather than picking one silently.

```sql conventions
select
    count(*) as n_country_years,
    100.0 * avg(abs(hdd_minmax_total - hdd_total) / hdd_total) as average_gap,
    100.0 * max(abs(hdd_minmax_total - hdd_total) / hdd_total) as widest_gap,
    100.0 * max(abs(hdd_minmax_total - hdd_total) / hdd_total)
        filter (where hdd_total >= 1000) as widest_with_heating_season,
    count(*) filter (where hdd_total < 1000) as n_barely_heated
from warehouse.country_weather_year
where year_is_complete and hdd_total > 0
```

```sql convention_worst
select
    country_name,
    cast(cast(year as integer) as varchar) as year_label,
    hdd_total,
    hdd_minmax_total,
    100.0 * (hdd_minmax_total - hdd_total) / hdd_total as gap
from warehouse.country_weather_year
where year_is_complete and hdd_total > 0
order by abs(hdd_minmax_total - hdd_total) / hdd_total desc
limit 8
```

Over <Value data={conventions} column=n_country_years fmt='#,##0'/> complete country-years the two conventions differ by <Value data={conventions} column=average_gap fmt='0.0"%"'/> on average and by as much as <Value data={conventions} column=widest_gap fmt='0.0"%"'/> at the extreme.

The extreme is a small-denominator effect rather than a measurement problem, and
the table below shows it: the worst disagreements are all places with barely any
heating season, where a few degree days either way is a large share of a small
total. Restricted to country-years with a real winter — a thousand degree days or
more — the widest gap is <Value data={conventions} column=widest_with_heating_season fmt='0.0"%"'/> and <Value data={conventions} column=n_barely_heated/> of the country-years fall below that line.

<DataTable data={convention_worst}>
    <Column id=country_name title="Country"/>
    <Column id=year_label title="Year"/>
    <Column id=hdd_total title="Mean-based" fmt='#,##0'/>
    <Column id=hdd_minmax_total title="Min/max-based" fmt='#,##0'/>
    <Column id=gap title="Difference" fmt='0.0"%"'/>
</DataTable>

<Alert status=warning>

**A degree-day total lifted out of this warehouse is meaningless without its base
and its convention.** Both travel on every row for that reason: the base
temperature as a column of its own, and each convention as its own total. Settle
either one in a config file instead and the number walks away from the only thing
that says what it means.

</Alert>

## A capital is not a country

The archive holds one grid cell per country, at its capital city. That is a
coarse proxy for national heating demand and the model says so with a number
rather than a caveat.

```sql grid
select
    country_name,
    country_iso3,
    max(grid_distance_km) as grid_distance_km
from warehouse.country_weather_year
group by 1, 2
order by 3 desc
limit 8
```

```sql grid_summary
select
    avg(grid_distance_km) as average_km,
    max(grid_distance_km) as furthest_km
from (select distinct country_iso3, grid_distance_km from warehouse.country_weather_year)
```

ERA5 answers on a 0.25-degree grid, so a request snaps to the nearest cell centre
and the response reports where it actually landed. Averaged over the capitals
that displacement is <Value data={grid_summary} column=average_km fmt='0.0'/> km, and the furthest capital sits <Value data={grid_summary} column=furthest_km fmt='0.0'/> km from the cell that answered for it.

<DataTable data={grid}>
    <Column id=country_name title="Country"/>
    <Column id=grid_distance_km title="Capital to grid cell, km" fmt='0.0'/>
</DataTable>

That displacement is small and it is not the approximation that matters. The
approximation that matters has no number here at all: Madrid's climate is not
Spain's, and one cell stands in for a whole national population. Comparing a
country against *itself* across years — which is what every degree-day column on
the weather section is used for — survives that. Comparing two countries against each other
does not, and no column in this section should be read as a ranking of national
climate.

## Limitations

- **Europe only.** The scope was chosen to match the Eurostat electricity price
  series exactly, so the two join with no gaps. Joined to the global emissions
  data it leaves the rest of the world null, the same way the electricity price
  column already does.
- **One cell per country.** See [A capital is not a country](#a-capital-is-not-a-country): fine for a country against itself, weak
  for one country against another. A population-weighted average over many cells
  is the honest version and costs many times the API budget this source has.
- **The recent tail is preliminary.** Open-Meteo serves ERA5T within a day or two
  of real time and Copernicus replaces it with final ERA5 two to three months
  later, so rows inside the last ninety days can change value between builds.
  Rows older than that are frozen, because they are carried forward between
  releases rather than refetched.
- **Degree days are a demand proxy, not demand.** They know nothing about
  building stock, insulation, occupancy or what a country heats with. A cold
  country with well-insulated housing and a mild one without can land the same
  way round here and the opposite way round on a gas bill.
- **A correlation is not a causal test, in either direction.** For prices, the
  finding is that the simplest weather explanation does not fit, which is what a
  control variable is for; it is not evidence for any particular alternative.
  For emissions the fit is real, but it carries whatever else moved with the
  winters: the gas crisis of 2022 and 2023 cut demand in two mild years, and
  some of that fall is in the fit too.
- **Winters are continent-wide.** Most of the emissions signal is the whole
  panel having a cold or a mild year together. Comparing countries within one
  year, which strips that out, leaves a much weaker relationship, so read the
  per-country chart as each country against its own history.

The tables are `marts.fct_country_weather_year` (these pages) and
`staging.stg_weather_daily` (the daily grain underneath it, one row per country
and date). Both ship in the
[data release](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/releases/latest),
along with the raw ERA5 landing table, which is the one table in this warehouse a
rebuild cannot reproduce inside the source's daily budget.
