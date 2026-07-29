---
title: Emissions, Energy & Development
description: CO₂, energy mix and human development across countries, built from OWID and World Bank data.
---

How does a country's energy mix relate to its emissions and human development?
This dashboard is built entirely from the `marts.fct_emissions_energy` table
produced by the dbt + Polars pipeline in this repo.

```sql years
select distinct year
from warehouse.emissions_energy
where year between 1990 and 2022
order by year desc
```

<Dropdown data={years} name=year value=year defaultValue=2019 title="Year"/>

```sql kpis
select
    count(*)                     as n_countries,
    avg(life_expectancy)         as avg_life_expectancy,
    avg(renewables_share_pct)    as avg_renewables
from warehouse.emissions_energy
where year = ${inputs.year.value}
  and life_expectancy is not null
```

<Grid cols=3>
    <BigValue data={kpis} value=n_countries title="Countries reporting"/>
    <BigValue data={kpis} value=avg_life_expectancy fmt="0.0" title="Avg life expectancy (yrs)"/>
    <BigValue data={kpis} value=avg_renewables fmt='0.0"%"' title="Avg renewables share"/>
</Grid>

## Renewables adoption vs. life expectancy

Each bubble is a country in <Value data={years} column=year/>, sized by population
and colored by World Bank income group.

```sql renew_vs_life
select
    country_name,
    income_group,
    region,
    renewables_share_pct as renewables_share,
    life_expectancy,
    population,
    co2_per_capita
from warehouse.emissions_energy
where year = ${inputs.year.value}
  and renewables_share_pct is not null
  and life_expectancy is not null
```

<BubbleChart
    data={renew_vs_life}
    x=renewables_share
    y=life_expectancy
    size=population
    series=income_group
    xAxisTitle="Renewables share of energy (%)"
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
    yAxisTitle="kg CO₂ per $ GDP"
/>

## Does more renewable energy mean cheaper power? (EU, <Value data={years} column=year/>)

Household electricity prices (incl. all taxes, Eurostat) against each EU country's
renewable share of energy for the selected year. The relationship is messy; grid,
tax and policy choices dominate.

```sql eu_price_vs_renew
select
    country_name,
    income_group,
    renewables_share_pct as renewables_share,
    electricity_price_eur_kwh,
    population
from warehouse.emissions_energy
where year = ${inputs.year.value}
  and electricity_price_eur_kwh is not null
  and renewables_share_pct is not null
```

<ScatterPlot
    data={eu_price_vs_renew}
    x=renewables_share
    y=electricity_price_eur_kwh
    series=income_group
    xAxisTitle="Renewables share of energy (%)"
    yAxisTitle="Household electricity price (€/kWh)"
    tooltipTitle=country_name
/>

## Most expensive EU electricity (<Value data={years} column=year/>)

```sql eu_prices
select
    country_name,
    electricity_price_eur_kwh,
    renewables_share_pct,
    internet_users_pct
from warehouse.emissions_energy
where year = ${inputs.year.value}
  and electricity_price_eur_kwh is not null
order by electricity_price_eur_kwh desc
limit 10
```

<DataTable data={eu_prices} rows=10>
    <Column id=country_name title="Country"/>
    <Column id=electricity_price_eur_kwh title="€ / kWh" fmt="0.000"/>
    <Column id=renewables_share_pct title="Renewables %" fmt="0.0"/>
    <Column id=internet_users_pct title="Internet users %" fmt="0.0"/>
</DataTable>

## Most carbon-efficient economies (<Value data={years} column=year/>)

Lowest CO₂ per dollar of GDP among countries reporting in the selected year.

```sql cleanest
select
    country_name,
    income_group,
    co2_per_gdp_const_usd,
    gdp_per_capita_usd,
    renewables_share_pct
from warehouse.co2_intensity
where year = ${inputs.year.value}
order by co2_per_gdp_const_usd asc
limit 10
```

<DataTable data={cleanest} rows=10>
    <Column id=country_name title="Country"/>
    <Column id=income_group title="Income group"/>
    <Column id=co2_per_gdp_const_usd title="CO₂ / $ GDP" fmt="0.000"/>
    <Column id=gdp_per_capita_usd title="GDP per capita" fmt="usd0"/>
    <Column id=renewables_share_pct title="Renewables %" fmt="0.0"/>
</DataTable>

---

<small>Sources: <a href="https://github.com/owid/co2-data">OWID CO₂</a>,
<a href="https://github.com/owid/energy-data">OWID Energy</a>,
<a href="https://databank.worldbank.org/source/world-development-indicators">World Bank WDI</a>,
<a href="https://ec.europa.eu/eurostat/databrowser/view/nrg_pc_204">Eurostat electricity prices</a>.
Built with dbt, DuckDB, Polars & Evidence.</small>
