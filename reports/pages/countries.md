---
title: Country Explorer
description: CO₂, energy mix and human development across countries, built from OWID and World Bank data.
sidebar_position: 7
---

Every country, for any year you pick, and any one country over time. Nothing here
is a fixed conclusion; the [nine findings](/findings) draw those from the same
data.

```sql latest_years
select * from warehouse.latest_years
```

```sql years
-- The mart sits on a country-year spine, so its latest year is whichever source
-- is furthest ahead (Eurostat prices), and that year carries prices and little
-- else. Offer only the years the electricity series (~210 countries) covers.
select distinct year
from warehouse.emissions_energy
where year >= 1990
  and year <= (select elec_year from ${latest_years})
  and life_expectancy is not null
order by year desc
```

<Dropdown data={years} name=year value=year defaultValue={years[0].year} title="Year"/>

```sql kpis
-- Every measure over the same denominator: the countries reporting all three in
-- the selected year.
select
    count(*)                            as n_countries,
    avg(life_expectancy)                as avg_life_expectancy,
    avg(carbon_intensity_elec_g_kwh)    as avg_grid_intensity,
    avg(low_carbon_share_elec_pct)      as avg_low_carbon
from warehouse.emissions_energy
where year = ${inputs.year.value}
  and life_expectancy is not null
  and carbon_intensity_elec_g_kwh is not null
  and low_carbon_share_elec_pct is not null
```

<Grid cols=4>
    <BigValue data={kpis} value=n_countries title="Countries reporting all three"/>
    <BigValue data={kpis} value=avg_life_expectancy fmt="0.0" title="Avg life expectancy (yrs)"/>
    <BigValue data={kpis} value=avg_grid_intensity fmt="#,##0" title="Avg grid (gCO₂/kWh)"/>
    <BigValue data={kpis} value=avg_low_carbon fmt='0.0"%"' title="Avg low-carbon electricity"/>
</Grid>

## Carbon intensity of the grid, {inputs.year.label}

```sql grid_map
-- Capped at 900 g for the colour; the tooltip shows the real figure.
select
    country_iso3,
    country_name,
    carbon_intensity_elec_g_kwh,
    coal_share_elec_pct,
    least(carbon_intensity_elec_g_kwh, 900) as "gCO₂ per kWh"
from warehouse.emissions_energy
where year = ${inputs.year.value}
  and carbon_intensity_elec_g_kwh is not null
```

<!-- `../`, because this page is served at `<base>/<page>/` and the two files sit
at the site root; a bare name resolves against the page and 404s. -->
<AreaMap
    data={grid_map}
    areaCol=country_iso3
    geoJsonUrl="../world-countries.geojson"
    geoId=iso3
    value="gCO₂ per kWh"
    valueFmt="#,##0"
    colorPalette={['#d6e6f7', '#8fb8e6', '#eda100', '#b5530a', '#5a2403']}
    basemap="../blank-tile.png"
    startingLat=30
    startingLong=10
    startingZoom=1
    height=400
    title="gCO₂ per kWh generated (900 or more shown as 900)"
    tooltip={[
        {id: 'country_name', showColumnName: false, valueClass: 'font-semibold'},
        {id: 'carbon_intensity_elec_g_kwh', title: 'gCO₂ per kWh', fmt: '#,##0'},
        {id: 'coal_share_elec_pct', title: 'Coal share %', fmt: '0'}
    ]}
/>

<style>
    :global(.leaflet-container) { background: transparent !important; }
</style>

Coal-heavy grids sit near 800 g, nuclear and hydro grids under 50. Gas ranges from
about 400 with modern plant to 700 with old plant or oil, which is why grids with
no coal can still be dark.

## Clean electricity and life expectancy, {inputs.year.label}

```sql clean_elec_vs_life
select
    country_name,
    income_group,
    low_carbon_share_elec_pct as low_carbon_share,
    life_expectancy,
    population
from warehouse.emissions_energy
where year = ${inputs.year.value}
  and low_carbon_share_elec_pct is not null
  and life_expectancy is not null
```

<BubbleChart
    data={clean_elec_vs_life}
    x=low_carbon_share
    y=life_expectancy
    size=population
    series=income_group
    seriesColors={{
        'High income': ['#2a78d6', '#3987e5'],
        'Upper middle income': ['#eda100', '#c98500'],
        'Lower middle income': ['#e87ba4', '#d55181'],
        'Low income': ['#008300', '#008300']
    }}
    xAxisTitle="Low-carbon share of electricity (%)"
    yAxisTitle="Life expectancy (years)"
    tooltipTitle=country_name
/>

Bubbles are sized by population. Read the spread, not a trend: a high low-carbon
share is as easily one large dam in a low-income country as a deliberate build-out
in a rich one.

## Electricity prices in Europe, {inputs.year.label}

```sql eu_price_vs_clean
select
    country_name,
    income_group,
    low_carbon_share_elec_pct as low_carbon_share,
    electricity_price_eur_kwh
from warehouse.emissions_energy
where year = ${inputs.year.value}
  and electricity_price_eur_kwh is not null
  and low_carbon_share_elec_pct is not null
```

```sql partial_price_years
-- Eurostat publishes each year in two halves and the annual column averages
-- whichever have landed, so some country-years are a half-year in an annual
-- costume. Report the count rather than dropping them.
select count(*) as n_partial
from warehouse.emissions_energy
where year = ${inputs.year.value}
  and price_is_partial_year
```

<ScatterPlot
    data={eu_price_vs_clean}
    x=low_carbon_share
    y=electricity_price_eur_kwh
    series=income_group
    seriesColors={{
        'High income': ['#2a78d6', '#3987e5'],
        'Upper middle income': ['#eda100', '#c98500'],
        'Lower middle income': ['#e87ba4', '#d55181'],
        'Low income': ['#008300', '#008300']
    }}
    yFmt="0.00"
    xAxisTitle="Low-carbon share of electricity (%)"
    yAxisTitle="Household price, € per kWh"
    tooltipTitle=country_name
/>

Household prices with all taxes, against the low-carbon share. Cleaner power does
not mean cheaper power: tax, network and policy choices dominate. **[What the annual average hides →](/countries/half-years)**

{#if partial_price_years.length > 0 && partial_price_years[0].n_partial > 0}

In {inputs.year.label}, <Value data={partial_price_years} column=n_partial/> of the countries plotted have only one of the year's two half-years published, so their figure is that half alone.

{/if}

## One country over time

```sql country_list
-- Keyed on the code, not the name: a name with an apostrophe (Côte d'Ivoire)
-- would end the string literal in the query below.
select distinct country_iso3, country_name
from warehouse.emissions_energy
where region is not null and co2_mt is not null
order by country_name
```

<Dropdown data={country_list} name=country value=country_iso3 label=country_name defaultValue="GBR" title="Country"/>

```sql one_country
select
    year,
    co2_per_capita,
    carbon_intensity_elec_g_kwh
from warehouse.emissions_energy
where country_iso3 = '${inputs.country.value}'
  and year >= 1990
  and (co2_per_capita is not null or carbon_intensity_elec_g_kwh is not null)
order by year
```

<Grid cols=2>

<LineChart
    data={one_country}
    x=year
    y=co2_per_capita
    xFmt="0"
    yFmt="0.0"
    yMin=0
    lineColor="#b5530a"
    echartsOptions={{xAxis: {min: 'dataMin', max: 'dataMax'}}}
    title="CO₂ per person, tonnes"
/>

<LineChart
    data={one_country}
    x=year
    y=carbon_intensity_elec_g_kwh
    xFmt="0"
    yFmt="#,##0"
    yMin=0
    lineColor="#2a78d6"
    echartsOptions={{xAxis: {min: 'dataMin', max: 'dataMax'}}}
    title="Grid carbon intensity, gCO₂ per kWh"
/>

</Grid>

## Carbon intensity of the economy, by income group

```sql co2_intensity_by_income
-- Each country in the group it held *that year*, and each group's emissions over
-- its real output. See sources/warehouse/co2_intensity_by_income.sql.
select
    year,
    income_group,
    kg_co2_per_usd
from warehouse.co2_intensity_by_income
where basis = 'as_classified'
  and year >= 1990
order by year
```

<LineChart
    data={co2_intensity_by_income}
    x=year
    y=kg_co2_per_usd
    yFmt="0.00"
    xFmt="0"
    series=income_group
    seriesColors={{
        'High income': ['#2a78d6', '#3987e5'],
        'Upper middle income': ['#eda100', '#c98500'],
        'Lower middle income': ['#e87ba4', '#d55181'],
        'Low income': ['#008300', '#008300']
    }}
    echartsOptions={{xAxis: {min: 'dataMin', max: 'dataMax'}}}
    title="kg of CO₂ per dollar of real GDP"
/>

Every country is counted in the group the World Bank placed it in *that year*, so
the low-income line steps down each time a large emitter leaves the group: China
in 1997, India in 2007. **[Why the grouping matters →](/countries/income-groups)**

## The most carbon-efficient economies, {inputs.year.label}

```sql cleanest
select
    country_name,
    income_group,
    co2_per_gdp_const_usd,
    gdp_per_capita_usd,
    population / 1000000 as population_m
from warehouse.co2_intensity
where year = ${inputs.year.value}
  and population > 5000000
order by co2_per_gdp_const_usd asc
limit 10
```

<DataTable data={cleanest} rows=10>
    <Column id=country_name title="Country"/>
    <Column id=income_group title="Income group"/>
    <Column id=co2_per_gdp_const_usd title="kg CO₂ / $ GDP" fmt="0.000" contentType=bar/>
    <Column id=gdp_per_capita_usd title="GDP per capita" fmt="usd0"/>
    <Column id=population_m title="Population (m)" fmt="#,##0"/>
</DataTable>

Countries over 5 million people; without the floor this is a list of financial
micro-states. Low CO₂ per dollar has three causes: clean power (Sweden, France),
industry that happens elsewhere ([finding 4](/findings/offshoring)), or little
commercial energy use at all. Ireland's figure is also flattered by profit-shifting
into its GDP.

---

<small>Sources: <a href="https://github.com/owid/co2-data">OWID CO₂</a>,
<a href="https://github.com/owid/energy-data">OWID Energy</a>,
<a href="https://databank.worldbank.org/source/world-development-indicators">World Bank WDI</a>,
<a href="https://ec.europa.eu/eurostat/databrowser/view/nrg_pc_204">Eurostat electricity prices</a>.</small>
