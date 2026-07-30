---
title: Explore
description: CO₂, energy mix and human development across countries, built from OWID and World Bank data.
---

Pick a year and every chart below re-queries the warehouse. All of it comes from
`marts.fct_emissions_energy`, built by the dlt + dbt + Polars pipeline in this
repo. For a fixed set of write-ups on the same data, see the
[findings](/findings).

```sql latest_years
select * from warehouse.latest_years
```

```sql years
-- The mart sits on a country-year spine, so its latest year is whichever source
-- is furthest ahead (Eurostat prices, 2025), and that year carries prices and
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

## Clean electricity vs. life expectancy ({inputs.year.label})

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

Each bubble is a country, sized by population and coloured by World Bank income
group. The x-axis is the low-carbon share of *electricity* (renewables plus
nuclear) rather than the renewables share of all energy: same idea, roughly 210
countries instead of 79, because OWID's broad-coverage series is the electricity
mix. Read the spread rather than a trend line. A high low-carbon share is as
easily one big hydroelectric dam in a low-income country as a deliberate
build-out in a rich one.

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

## Carbon intensity of the economy, over time

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

Average CO₂ per dollar of GDP by income group: roughly, how much emissions each
dollar of economic output carries.

## Carbon intensity of the grid ({inputs.year.label})

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

Grams of CO₂ per kWh generated, for countries with a grid big enough for the
number to be stable (over 50 TWh). Coal-heavy grids sit near 800, gas near 400,
nuclear and hydro grids under 50.

## Does cleaner electricity mean cheaper power? (EU, {inputs.year.label})

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

```sql partial_price_years
-- Eurostat publishes each year in two halves and the annual column averages
-- whichever have landed, so some country-years are a half-year in an annual
-- costume. Report the count rather than dropping them: in 2007 that would be 23
-- of the 27 priced countries.
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
    xAxisTitle="Low-carbon share of electricity (%)"
    yAxisTitle="Household electricity price (€/kWh)"
    tooltipTitle=country_name
/>

Household electricity prices (including all taxes, from Eurostat) against each EU
country's low-carbon share of electricity. The relationship is messy; grid, tax
and policy choices dominate. Using the electricity share rather than the
primary-energy one keeps all 39 priced countries in the chart, since nine of them
have no `renewables_share_pct` at all.

{#if partial_price_years.length > 0 && partial_price_years[0].n_partial > 0}

One caveat on the prices in {inputs.year.label}:
<Value data={partial_price_years} column=n_partial/> of the countries plotted have
only one of the year's two half-years published, so their figure is that half
rather than an average of both. The last section on this page is about what that
averaging costs.

{/if}

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

## Most carbon-efficient economies ({inputs.year.label})

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

Lowest CO₂ per dollar of real GDP, among countries of over 5 million people.
Without a size floor this table is a list of financial and tourism micro-states
(Macao, Bermuda, Malta) whose ranking says more about having no industry than
about having clean industry.

Even with the floor, read it carefully. Low CO₂ per dollar has two very different
causes: a genuinely low-carbon economy such as Sweden or France, running on
nuclear and hydro, and an economy whose industrial production happens somewhere
else, which finding 4 on the [findings page](/findings) quantifies. Ireland's
number is also inflated by the multinational profit-shifting that distorts its
GDP denominator.

## What the annual average costs

*This section covers the whole series rather than the selected year.*

```sql volatile_countries
-- The countries with the largest single half-over-half move in cents, not
-- percent: a percent ranking promotes small markets moving off a low base.
select country_name
from warehouse.eu_electricity_prices_semiannual
group by country_name
order by max(abs(change_vs_previous_half_eur_kwh)) desc nulls last
limit 6
```

```sql semiannual_prices
select
    period_start_date,
    country_name,
    electricity_price_eur_kwh
from warehouse.eu_electricity_prices_semiannual
where country_name in (select country_name from ${volatile_countries})
order by period_start_date
```

<LineChart
    data={semiannual_prices}
    x=period_start_date
    y=electricity_price_eur_kwh
    series=country_name
    yAxisTitle="€ / kWh (household, all taxes)"
    xAxisTitle="Half-year"
    yFmt="0.00"
/>

Eurostat publishes household prices **twice a year**, and every chart above uses
an annual average of the two halves. That is what the `(country_iso3, year)`
grain costs, and the average is not a neutral summary.
`marts.fct_eu_electricity_prices_semiannual` keeps the published grain beside it,
and the difference is the 2021–23 energy crisis: the mean absolute half-over-half
change was **19%** across countries in 2022 and 13% in 2023, against 3–4% through
the 2010s.

The spikes above are single half-years. Averaged into an annual figure they
become a smooth rise, which reads as a gradual squeeze rather than the step
change households actually saw.

```sql biggest_half_moves
select
    country_name,
    period,
    electricity_price_eur_kwh,
    electricity_price_eur_kwh - change_vs_previous_half_eur_kwh as previous_price,
    change_vs_previous_half_pct,
    avg(electricity_price_eur_kwh) over (partition by country_iso3, year) as annual_average
from warehouse.eu_electricity_prices_semiannual
where change_vs_previous_half_eur_kwh is not null
order by abs(change_vs_previous_half_pct) desc
limit 8
```

<DataTable data={biggest_half_moves} rows=8>
    <Column id=country_name title="Country"/>
    <Column id=period title="Half-year" align=left/>
    <Column id=previous_price title="Previous half" fmt="0.000"/>
    <Column id=electricity_price_eur_kwh title="This half" fmt="0.000"/>
    <!-- Two clauses: a bare +0"%" renders -77% as "-+77%". -->
    <Column id=change_vs_previous_half_pct title="Change" fmt='+0"%";-0"%"'/>
    <Column id=annual_average title="Year's average" fmt="0.000"/>
</DataTable>

The Netherlands is the clearest case, and the one that should make you distrust
any annual number here: €0.034/kWh in 2022-S1 against €0.142 in S2, as that
year's energy-tax cuts landed in the first half. The annual average of €0.088 is
a price no Dutch household paid in either half. The low figure is real and
published rather than an ingest bug, which is why the staging test on this column
has a floor of zero and no minimum above it.

---

<small>Sources: <a href="https://github.com/owid/co2-data">OWID CO₂</a>,
<a href="https://github.com/owid/energy-data">OWID Energy</a>,
<a href="https://databank.worldbank.org/source/world-development-indicators">World Bank WDI</a>,
<a href="https://ec.europa.eu/eurostat/databrowser/view/nrg_pc_204">Eurostat electricity prices</a>.
Built with dbt, DuckDB, Polars & Evidence.</small>
