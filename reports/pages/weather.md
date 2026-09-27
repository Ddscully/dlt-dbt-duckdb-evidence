---
title: Weather
description: Whether it was a colder year is the first explanation to rule out before crediting an energy number to policy, price or efficiency. Across European capitals it explains none of what household electricity prices did, and much of the year-to-year swing in emissions.
sidebar_position: 5
---

*Was it just colder?* is the question to dispose of before crediting an energy or
price movement to anything else. This page answers it with daily ERA5 reanalysis
for the capital of every country in Eurostat's electricity price series, turned
into heating degree days.

```sql panel
select
    count(distinct country_iso3) as n_countries,
    count(distinct year) filter (where year_is_complete) as n_complete_years
from warehouse.country_weather_year
```

```sql pooled
select
    count(*) as n_observations,
    corr(hdd_change, price_change) as pooled_correlation,
    100 * regr_r2(price_change, hdd_change) as variance_explained
from warehouse.weather_price_pairs
```

<Grid cols=4>
    <BigValue data={panel} value=n_countries title="Capitals in the archive"/>
    <BigValue data={panel} value=n_complete_years title="Complete calendar years"/>
    <BigValue data={pooled} value=n_observations fmt='#,##0' title="Country-years compared"/>
    <BigValue data={pooled} value=variance_explained fmt='0.0"%"' title="Of price movement explained by weather"/>
</Grid>

## A colder year does not explain electricity prices

```sql price_scatter
select country_name, cast(cast(year as integer) as varchar) as year_label, hdd_change, price_change
from warehouse.weather_price_pairs
```

<ScatterPlot
    data={price_scatter}
    x=hdd_change
    y=price_change
    color="#2a78d6"
    opacity=0.5
    xFmt='0"%"'
    yFmt='0"%"'
    xAxisTitle="Change in heating degree days"
    yAxisTitle="Change in electricity price"
    tooltipTitle=country_name
    title="Each country-year against the one before"
>
    <ReferenceLine y=0 label=" "/>
</ScatterPlot>

A shapeless cloud: over <Value data={pooled} column=n_observations fmt='#,##0'/> country-years the correlation is <Value data={pooled} column=pooled_correlation fmt='0.00'/> and no single year shows a meaningful relationship either. Household tariffs move on tax, network cost and gas exposure, not on the winter. **[Prices →](/weather/prices)**

## It does explain much of the swing in emissions

```sql emissions_lines
-- Heating demand averaged over the capitals; CO₂ as the panel total, so a large
-- emitter counts for what it emits.
with by_year as (
    select
        year,
        avg(hdd_change)                                  as hdd_change,
        100.0 * (sum(co2_mt) / sum(previous_co2_mt) - 1) as co2_change
    from warehouse.weather_emissions_pairs
    where co2_change is not null
    group by year
)

select year, 'Heating demand' as measure, hdd_change as pct from by_year
union all
select year, 'CO₂ emitted', co2_change from by_year
order by year
```

```sql emissions_fit
-- 2020 and 2021 are left out: a mild winter with a lockdown, then a cold one with
-- a rebound, both lining up with the weather by coincidence.
with by_year as (
    select
        year,
        avg(hdd_change)                                     as hdd_change,
        100.0 * (sum(co2_mt) / sum(previous_co2_mt) - 1)     as co2_change
    from warehouse.weather_emissions_pairs
    where co2_change is not null
      and not is_pandemic_year
    group by year
)

select
    count(*)                                as n_year_pairs,
    100 * regr_r2(co2_change, hdd_change)   as variance_explained
from by_year
```

<LineChart
    data={emissions_lines}
    x=year
    y=pct
    series=measure
    seriesColors={{
        'Heating demand': ['#2a78d6', '#3987e5'],
        'CO₂ emitted': ['#eb6834', '#d95926']
    }}
    markers=true
    xFmt="0"
    yFmt='0"%"'
    echartsOptions={{xAxis: {min: 'dataMin', max: 'dataMax'}}}
    title="Change on the year before, across the European panel"
>
    <ReferenceLine y=0 label=" "/>
</LineChart>

{#if emissions_fit[0].n_year_pairs >= 5}

Outside the pandemic years, the winter alone accounts for <Value data={emissions_fit} column=variance_explained fmt='0"%"'/> of the year-to-year swing in what these countries emit. An emissions cut reported after a mild winter may be the weather. **[Emissions →](/weather/emissions)**

{:else}

The archive is still too short to fit this relationship; it deepens with every
release. **[Emissions →](/weather/emissions)**

{/if}

## Every winter is milder than it used to be

```sql stripes
-- Each capital against its own average over the complete years, so a cold
-- capital and a warm one share a scale. Only one grid cell per country, so read a
-- row against itself, never one row against another.
--
-- The x axis is a category, so a year with no row would simply not be drawn and
-- the archive's gap would close up. Every year in the range gets a row, and a
-- missing one carries a null anomaly, which `nullsZero=false` leaves blank.
with complete as (
    select
        country_name,
        cast(year as integer) as year,
        temp_mean_c,
        avg(temp_mean_c) over (partition by country_iso3) as country_mean
    from warehouse.country_weather_year
    where year_is_complete
),

grid as (
    select c.country_name, y.year
    from (select distinct country_name from complete) c
    cross join (
        select unnest(generate_series(min(year), max(year))) as year from complete
    ) y
)

select
    g.country_name,
    cast(g.year as varchar)         as year_label,
    c.temp_mean_c - c.country_mean as anomaly_c
from grid g
left join complete c on c.country_name = g.country_name and c.year = g.year
```

```sql trend
with by_year as (
    select cast(year as integer) as year, avg(temp_mean_c) as mean_c
    from warehouse.country_weather_year
    where year_is_complete
    group by 1
)
select count(*) as n_years, 10 * regr_slope(mean_c, year) as deg_c_per_decade
from by_year
```

<Heatmap
    data={stripes}
    x=year_label
    y=country_name
    value=anomaly_c
    valueFmt='0.0'
    xSort=year_label
    ySort=country_name
    valueLabels=false
    nullsZero=false
    cellHeight=12
    min={-2}
    max={2}
    colorPalette={['#2a78d6', '#f5f5f5', '#d95926']}
    title="Mean temperature against each capital's own average, °C"
    subtitle="Blue colder, red warmer, grey where the archive has no year; complete calendar years only"
/>

{#if trend[0].n_years >= 8}

The red gathers on the right in nearly every row. Fitted across <Value data={trend} column=n_years/> complete years, these capitals warm by <Value data={trend} column=deg_c_per_decade fmt='0.0'/>°C a decade, and heating demand falls with them. **[The archive →](/weather/archive)**

{:else}

The archive is too short to fit a trend through yet. **[The archive →](/weather/archive)**

{/if}

<Alert status=warning>

**One grid cell per country, at its capital.** Fine for a country against its own
history, which is how every chart here reads; not a ranking of national climate.
**[Method and limits →](/weather/method)**

</Alert>

<small>Weather data by <a href="https://open-meteo.com/">Open-Meteo</a> (CC BY
4.0), derived from ERA5 reanalysis produced by
<a href="https://www.ecmwf.int/">ECMWF</a> for the
<a href="https://climate.copernicus.eu/">Copernicus Climate Change Service</a>.</small>
