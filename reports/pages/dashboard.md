---
title: Explore
description: CO₂, energy mix and human development across countries, built from OWID and World Bank data.
---

How does a country's energy mix relate to its emissions and human development?
This interactive dashboard is built entirely from the `marts.fct_emissions_energy`
table produced by the dbt + Polars pipeline in this repo — pick a year and every
chart below re-queries the warehouse live. For a fixed set of write-ups on the
same data, see the [findings](/).

```sql latest_years
select * from warehouse.latest_years
```

```sql years
-- The mart sits on a country-year spine, so its latest year is whichever source
-- is furthest ahead (Eurostat prices, 2025) — and that year carries prices and
-- nothing else. Offer only the years where the charts on this page have a broad
-- enough sample to be worth drawing, which is the electricity series (~210
-- countries), not the primary-energy one (79 from 2024 on).
select distinct year
from warehouse.emissions_energy
where year >= 1990
  and year <= (select elec_year from ${latest_years})
  and life_expectancy is not null
order by year desc
```

<Dropdown data={years} name=year value=year defaultValue={years[0].year} title="Year"/>

```sql kpis
-- Every measure here is counted over the same denominator: the countries that
-- report all three in the selected year. Averaging life expectancy over 217
-- countries next to a renewables figure over 79 put two different worlds in the
-- same row of tiles.
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
    <BigValue data={kpis} value=n_countries title="Countries reporting all four"/>
    <BigValue data={kpis} value=avg_life_expectancy fmt="0.0" title="Avg life expectancy (yrs)"/>
    <BigValue data={kpis} value=avg_grid_intensity fmt="#,##0" title="Avg grid (gCO₂/kWh)"/>
    <BigValue data={kpis} value=avg_low_carbon fmt='0.0"%"' title="Avg low-carbon electricity"/>
</Grid>

## Clean electricity vs. life expectancy

Each bubble is a country in {inputs.year.label}, sized by population
and colored by World Bank income group.

The x-axis is the low-carbon share of *electricity* (renewables plus nuclear)
rather than the renewables share of all energy. Same idea, roughly 210 countries
instead of 79 — OWID's broad-coverage series is the electricity mix. Read the
spread, not a trend line: a high low-carbon share is as easily one big
hydroelectric dam in a low-income country as a deliberate build-out in a rich one.

```sql clean_elec_vs_life
select
    country_name,
    income_group,
    region,
    low_carbon_share_elec_pct as low_carbon_share,
    carbon_intensity_elec_g_kwh,
    life_expectancy,
    population,
    co2_per_capita
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

## Carbon intensity of the economy, over time

Average CO₂ per dollar of GDP by income group: roughly, how much emissions each
dollar of economic output carries.

```sql co2_intensity_by_income
select
    year,
    income_group,
    avg(co2_per_gdp_const_usd) as avg_co2_per_gdp
from warehouse.co2_intensity
where income_group is not null
  and year >= 1990
group by year, income_group
order by year
```

<LineChart
    data={co2_intensity_by_income}
    x=year
    y=avg_co2_per_gdp
    series=income_group
    seriesColors={{
        'High income': ['#2a78d6', '#3987e5'],
        'Upper middle income': ['#eda100', '#c98500'],
        'Lower middle income': ['#e87ba4', '#d55181'],
        'Low income': ['#008300', '#008300']
    }}
    yAxisTitle="kg CO₂ per $ GDP"
/>

## Does cleaner electricity mean cheaper power? (EU, {inputs.year.label})

Household electricity prices (incl. all taxes, Eurostat) against each EU country's
low-carbon share of electricity for the selected year. The relationship is messy;
grid, tax and policy choices dominate. Using the electricity share rather than the
primary-energy one also keeps all 39 priced countries in the chart — nine of them
have no `renewables_share_pct` at all.

```sql eu_price_vs_clean
select
    country_name,
    income_group,
    low_carbon_share_elec_pct as low_carbon_share,
    electricity_price_eur_kwh,
    population
from warehouse.emissions_energy
where year = ${inputs.year.value}
  and electricity_price_eur_kwh is not null
  and low_carbon_share_elec_pct is not null
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
    xAxisTitle="Low-carbon share of electricity (%)"
    yAxisTitle="Household electricity price (€/kWh)"
    tooltipTitle=country_name
/>

## Most expensive EU electricity ({inputs.year.label})

```sql eu_prices
select
    country_name,
    electricity_price_eur_kwh,
    low_carbon_share_elec_pct,
    carbon_intensity_elec_g_kwh
from warehouse.emissions_energy
where year = ${inputs.year.value}
  and electricity_price_eur_kwh is not null
order by electricity_price_eur_kwh desc
limit 10
```

<DataTable data={eu_prices} rows=10>
    <Column id=country_name title="Country"/>
    <Column id=electricity_price_eur_kwh title="€ / kWh" fmt="0.000"/>
    <Column id=low_carbon_share_elec_pct title="Low-carbon %" fmt="0.0"/>
    <Column id=carbon_intensity_elec_g_kwh title="gCO₂ / kWh" fmt="#,##0"/>
</DataTable>

## Carbon intensity of the grid ({inputs.year.label})

Grams of CO₂ per kWh generated, for the countries with a grid large enough for the
number to be stable (over 50 TWh). Coal-heavy grids sit near 800; gas near 400;
nuclear and hydro grids under 50.

```sql grid_intensity
select
    country_name,
    income_group,
    carbon_intensity_elec_g_kwh,
    coal_share_elec_pct,
    low_carbon_share_elec_pct,
    electricity_generation_twh
from warehouse.emissions_energy
where year = ${inputs.year.value}
  and carbon_intensity_elec_g_kwh is not null
  and electricity_generation_twh > 50
order by carbon_intensity_elec_g_kwh desc
limit 15
```

<BarChart
    data={grid_intensity}
    x=country_name
    y=carbon_intensity_elec_g_kwh
    swapXY=true
    sort=false
    color="#eb6834"
    labels=true
    labelFmt="#,##0"
    xAxisTitle="gCO₂ per kWh generated"
    yAxisTitle="Country"
/>

## Most carbon-efficient economies ({inputs.year.label})

Lowest CO₂ per dollar of real GDP, among countries of over 5 million people —
without a size floor this table is a list of financial and tourism micro-states
(Macao, Bermuda, Malta) whose ranking says more about having no industry than
about having clean industry.

Even with the floor, read it carefully. Low CO₂ per dollar has two very different
causes: a genuinely low-carbon economy (Sweden, France — nuclear and hydro), and
an economy whose industrial production happens somewhere else, which finding 4 on
the [findings page](/) quantifies. Ireland's number is also inflated by the
multinational profit-shifting that distorts its GDP denominator.

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
    <Column id=co2_per_gdp_const_usd title="CO₂ / $ GDP" fmt="0.000"/>
    <Column id=gdp_per_capita_usd title="GDP per capita" fmt="usd0"/>
    <Column id=population_m title="Population (m)" fmt="#,##0"/>
</DataTable>

---

<small>Sources: <a href="https://github.com/owid/co2-data">OWID CO₂</a>,
<a href="https://github.com/owid/energy-data">OWID Energy</a>,
<a href="https://databank.worldbank.org/source/world-development-indicators">World Bank WDI</a>,
<a href="https://ec.europa.eu/eurostat/databrowser/view/nrg_pc_204">Eurostat electricity prices</a>.
Built with dbt, DuckDB, Polars & Evidence.</small>
