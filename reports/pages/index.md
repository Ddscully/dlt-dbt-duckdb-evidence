---
title: What this warehouse answers
description: A working data warehouse covering carbon border costs, emission factors for disclosure, retail customer economics, currency effects, weather as a control variable and long-run emissions trends.
---

Six analyses built on public data, each ending in a decision somebody has to
make. Every figure is a live query, so the numbers move when the data does.

```sql latest_years
select * from warehouse.latest_years
```

```sql per_capita
-- The colour is capped at 20 t so a handful of Gulf producers above 30 t do not
-- wash out everyone else; the map's own `max` caps the colours but not the legend,
-- which still prints the uncapped maximum. The tooltip shows the real figure.
select
    country_iso3,
    country_name,
    co2_per_capita,
    least(co2_per_capita, 20) as "Tonnes per person"
from warehouse.emissions_energy
where year = (select co2_year from ${latest_years})
  and region is not null
  and co2_per_capita is not null
```

<!-- Both files are relative and served from `static/`, so they resolve under the
Pages base path and nothing is fetched from a third party. The blank tile stands in
for Evidence's default basemap, which is CARTO's tile server. -->
<AreaMap
    data={per_capita}
    areaCol=country_iso3
    geoJsonUrl="world-countries.geojson"
    geoId=iso3
    value="Tonnes per person"
    valueFmt="0"
    colorPalette={['#fdf1dc', '#eda100', '#b5530a', '#5a2403']}
    basemap="blank-tile.png"
    startingLat=30
    startingLong=10
    startingZoom=1
    height=420
    title="CO₂ per person, tonnes (20 or more shown as 20)"
    tooltip={[
        {id: 'country_name', showColumnName: false, valueClass: 'font-semibold'},
        {id: 'co2_per_capita', title: 'Tonnes per person', fmt: '0.0'}
    ]}
/>

<style>
    /* Leaflet paints its container grey behind the tiles; with no basemap that is
       the whole ocean. */
    :global(.leaflet-container) { background: transparent !important; }
</style>

<Grid cols=2>

<Group>

### [Nine Findings →](/findings)

```sql peak_index
with series as (
    select
        country_name,
        year,
        co2_mt,
        max(co2_mt) over (partition by country_iso3) as peak_mt
    from warehouse.emissions_energy
    where country_iso3 in ('GBR', 'USA', 'CHN', 'IND')
      and co2_mt is not null
)

select country_name, year, 100 * co2_mt / peak_mt as pct_of_peak
from series
where year >= 1960
order by country_name, year
```

<LineChart
    data={peak_index}
    x=year
    y=pct_of_peak
    series=country_name
    seriesColors={{
        'China': ['#eb6834', '#d95926'],
        'India': ['#eda100', '#c98500'],
        'United States': ['#2a78d6', '#3987e5'],
        'United Kingdom': ['#8a5fd6', '#7248c4']
    }}
    xFmt="0"
    yFmt="0"
    yMax=100
    chartAreaHeight=160
    echartsOptions={{xAxis: {min: 'dataMin', max: 'dataMax'}}}
    title="Emissions as % of each country's peak"
/>

Rich countries peaked decades ago; China and India have not. What six decades
of national emissions and energy data support.

</Group>

<Group>

### [CBAM Exposure →](/cbam)

```sql cbam_steel
select country_display_name, cbam_cost_2026_eur_per_t
from warehouse.cbam_exposure
where good_key = '72071190-semi-finished-products-of-iron-or-non-al'
  and country_display_name in ('United States', 'United Kingdom', 'Turkiye', 'China', 'India', 'Indonesia')
  and not is_fallback_table
order by cbam_cost_2026_eur_per_t
```

```sql cbam_steel_gap
-- A gap in euros, not a ratio: the cheapest source is a scrap route, so a ratio
-- mostly measures how small the denominator is (compliance-models skill).
select max(cbam_cost_2026_eur_per_t) - min(cbam_cost_2026_eur_per_t) as gap_eur
from ${cbam_steel}
```

<BarChart
    data={cbam_steel}
    x=country_display_name
    y=cbam_cost_2026_eur_per_t
    swapXY=true
    sort=false
    color="#b5530a"
    labels=true
    labelFmt='€#,##0'
    yFmt='€#,##0'
    chartAreaHeight=160
    yMax={800}
    title="Gross border cost per tonne of semi-finished steel, 2026"
/>

Between the cheapest and the dearest of these six sources, the same tonne of steel carries <Value data={cbam_steel_gap} column=gap_eur fmt='€#,##0'/> more in gross CBAM cost in 2026.

What the CBAM defaults put on imports of steel, cement, aluminium, fertiliser and hydrogen, before deductions.

</Group>

<Group>

### [Scope 2 Factors →](/scope2)

```sql grid_factors
select emission_factor_g_co2_per_kwh
from warehouse.grid_emission_factors
where is_latest_available
  and electricity_generation_twh > 10
```

<Histogram
    data={grid_factors}
    x=emission_factor_g_co2_per_kwh
    xAxisTitle="gCO₂ per kWh"
    fillColor="#2a78d6"
    chartAreaHeight=160
    title="Grid emission factors, countries over 10 TWh"
/>

An identical site reports a very different Scope 2 figure on its address alone.
The factor for every country, with its vintage and lineage.

</Group>

<Group>

### [Retail Transactions →](/retail)

```sql retail_monthly
select
    cast(invoice_month || '-01' as date) as month_start,
    sum(revenue_gbp)                     as revenue_gbp
from warehouse.retail_daily
group by invoice_month
order by invoice_month
```

<AreaChart
    data={retail_monthly}
    x=month_start
    y=revenue_gbp
    yFmt="gbp0k"
    fillColor="#1baf7a"
    lineColor="#1baf7a"
    chartAreaHeight=160
    title="One retailer's monthly net revenue"
/>

Where the revenue sits, who comes back, and three definitions that each change
the answer by six figures.

</Group>

<Group>

### [Currency →](/currency)

```sql eur_usd
select period_start_date, eur_kwh as euros, usd_kwh as dollars
from warehouse.eu_price_panel
order by period_start_date
```

<LineChart
    data={eur_usd}
    x=period_start_date
    y={['euros', 'dollars']}
    yFmt="0.00"
    seriesColors={{'Euros': '#2a78d6', 'Dollars': '#eb6834'}}
    chartAreaHeight=160
    title="European household electricity, per kWh"
/>

The same price rose by very different amounts depending on the currency it was
counted in, and when to use an average rate rather than a spot one.

</Group>

<Group>

### [Weather →](/weather)

```sql weather_emissions
-- The weather page's panel measure: heating demand averaged over the capitals,
-- CO₂ as the panel total, so a large emitter counts for what it emits.
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

<LineChart
    data={weather_emissions}
    x=year
    y=pct
    series=measure
    seriesColors={{
        'Heating demand': ['#2a78d6', '#3987e5'],
        'CO₂ emitted': ['#eb6834', '#d95926']
    }}
    xFmt="0"
    yFmt='0"%"'
    chartAreaHeight=160
    echartsOptions={{xAxis: {min: 'dataMin', max: 'dataMax'}}}
    title="Change on the year before, European panel"
>
    <ReferenceLine y=0 label=" "/>
</LineChart>

Before crediting an energy number to policy, rule out a cold year. Weather moves
Europe's emissions, and none of its electricity prices.

</Group>

</Grid>

To check a specific country or year rather than read a conclusion, use the
**[Country Explorer](/countries)**.

## Before you quote a number

<Alert status=warning>

**The engineering is built and measured; the analysis has not had the same
scrutiny.** Treat the conclusions here as illustrative. The Scope 2 worked example
runs on fabricated sites, and coverage thins unevenly between measures.
[What this is not, yet](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/blob/main/docs/FOR_REVIEWERS.md#0-what-this-is-not-yet).

</Alert>

- **[Coverage](/coverage)**: which countries and years each measure covers, and
  where two sources disagree.
- **[Restatements](/restatements)**: figures revised since this warehouse first
  recorded them.
- **[Pipeline](/pipeline)**: the operational state of the load itself.
- <!-- Plain HTML, relative: `dbt/` is a file `build_report` adds after
  Evidence builds. `rel="external"` keeps the prerender crawler from failing
  on it and the router from taking it; every route ends in `/`, so it
  resolves under any base path. -->
  **<a href="dbt/" rel="external">Data catalogue</a>**: every model and column,
  its contract, its tests and its lineage back to the source.

Seven public sources and one retailer's transaction log, loaded, modelled, tested
and released each month as a DuckDB file, with the published tables also as
Parquet.

---

<small>Sources: <a href="https://github.com/owid/co2-data">OWID CO₂</a>,
<a href="https://github.com/owid/energy-data">OWID Energy</a>,
<a href="https://databank.worldbank.org/source/world-development-indicators">World Bank WDI</a>,
<a href="https://ec.europa.eu/eurostat/databrowser/view/nrg_pc_204">Eurostat electricity prices</a>,
<a href="https://frankfurter.dev">ECB reference rates via Frankfurter</a>,
<a href="https://open-meteo.com/">Open-Meteo</a> (ERA5, Copernicus/ECMWF),
<a href="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=OJ%3AL_202502621">Implementing Regulation (EU) 2025/2621</a>,
<a href="https://archive.ics.uci.edu/dataset/502/online+retail+ii">UCI Online Retail II</a>.
Country outlines from <a href="https://www.naturalearthdata.com/">Natural Earth</a>.
Built with dlt, dbt, DuckDB, Polars, Dagster &amp; Evidence &mdash; every model,
test and workflow behind these numbers is on
<a href="https://github.com/Ddscully/dlt-dbt-duckdb-evidence">GitHub</a>, along
with a <a href="https://github.com/Ddscully/dlt-dbt-duckdb-evidence/releases/latest">monthly
release</a> of the whole warehouse as DuckDB and Parquet.</small>
