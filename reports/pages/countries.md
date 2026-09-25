---
title: Country Explorer
description: CO₂, energy mix and human development across countries, built from OWID and World Bank data.
sidebar_position: 7
---

Emissions, energy mix, electricity prices and living standards for every country,
for any year you pick. Nothing here is a fixed conclusion. The charts re-query on
each selection, so this is the page for checking a country or a year yourself.

For the write-ups that draw conclusions from the same data, see the
[nine findings](/findings).

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
-- Each country in the group it held *that year*, and each group's emissions over
-- its real output. See sources/warehouse/co2_intensity_by_income.sql for why
-- neither today's classification nor an average of country ratios will do.
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
    series=income_group
    seriesColors={{
        'High income': ['#2a78d6', '#3987e5'],
        'Upper middle income': ['#eda100', '#c98500'],
        'Lower middle income': ['#e87ba4', '#d55181'],
        'Low income': ['#008300', '#008300']
    }}
    yAxisTitle="kg CO₂ per $ GDP"
/>

How much CO₂ a dollar of each income group's output carries: the group's
emissions divided by its GDP in constant 2015 dollars, with every country counted
in the group the World Bank placed it in *that year*. That makes some of the
movement membership rather than intensity: the low-income line steps down each
time a large emitter leaves the group, China in 1997 (it was back for 1998),
India in 2007, and Vietnam and Uzbekistan in 2009.

Both halves of that sentence are choices, and each one changes the chart. The
classification moves: about half of the economies classified in 1990 are in a
different group today. And an average of country ratios weights Bhutan like
China, where a ratio of totals weights each economy by its size. This chart used
to make both choices the other way, grouping every year by today's
classification and averaging country by country, and that version told a
different story.

```sql income_basis_first_year
select min(year) as first_year
from warehouse.co2_intensity_by_income
where basis = 'as_classified'
  and year >= 1990
```

```sql income_basis_compare
-- The chart's first year on both bases, in income-ladder order. `today_mean` is
-- what the chart used to plot; `as_classified` is what it plots now.
select
    t.income_group,
    t.mean_country_kg_co2_per_usd as today_mean,
    c.kg_co2_per_usd              as as_classified,
    case t.income_group
        when 'High income' then 1
        when 'Upper middle income' then 2
        when 'Lower middle income' then 3
        when 'Low income' then 4
    end                           as rung
from warehouse.co2_intensity_by_income as t
inner join warehouse.co2_intensity_by_income as c
    on t.year = c.year and t.income_group = c.income_group
where t.basis = 'today'
  and c.basis = 'as_classified'
  and t.year = (select first_year from ${income_basis_first_year})
order by rung
```

```sql income_basis_tops
select
    cast(cast(max(t.year) as integer) as varchar)          as year_label,
    arg_max(t.income_group, t.mean_country_kg_co2_per_usd) as today_top,
    max(t.mean_country_kg_co2_per_usd)                     as today_top_value,
    arg_max(c.income_group, c.kg_co2_per_usd)              as classified_top,
    max(c.kg_co2_per_usd)                                  as classified_top_value
from warehouse.co2_intensity_by_income as t
inner join warehouse.co2_intensity_by_income as c
    on t.year = c.year and t.income_group = c.income_group
where t.basis = 'today'
  and c.basis = 'as_classified'
  and t.year = (select first_year from ${income_basis_first_year})
```

<DataTable data={income_basis_compare} rows=4 rowNumbers=false>
    <Column id=income_group title="Income group"/>
    <Column id=today_mean title="Today's groups, country average (kg/$)" fmt="0.00"/>
    <Column id=as_classified title="Groups as classified, by output (kg/$)" fmt="0.00"/>
</DataTable>

In <Value data={income_basis_tops} column=year_label/> the old chart ranked <Value data={income_basis_tops} column=today_top/> the most carbon-intensive group, at <Value data={income_basis_tops} column=today_top_value fmt="0.00"/> kg per dollar. Grouped as classified that year and weighted by output, the most carbon-intensive was <Value data={income_basis_tops} column=classified_top/> at <Value data={income_basis_tops} column=classified_top_value fmt="0.00"/> kg per dollar.

The top of the corrected ranking is mostly two countries. China was classified
low income until 1998 and India until 2006, and weighted by output they dominate
that group's figure through the 1990s. The old chart filed both under the groups
they hold today and then averaged each group country by country, so China counted
as one economy in fifty-odd, in a group it would not reach for another twenty
years. Every other income-group rollup on this site cuts to a single recent year,
where today's classification is the right one; over a trend it is not.

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
have no renewables figure at all.

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
an annual average of the two halves. That average is not a neutral summary, which
is why the warehouse keeps the published half-years beside it. The difference is
the 2021–23 energy crisis: the mean absolute half-over-half change was **19%**
across countries in 2022 and 13% in 2023, against 3–4% through the 2010s.

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
published, not a loading error, which is why the tests on this column allow it.

---

<small>Sources: <a href="https://github.com/owid/co2-data">OWID CO₂</a>,
<a href="https://github.com/owid/energy-data">OWID Energy</a>,
<a href="https://databank.worldbank.org/source/world-development-indicators">World Bank WDI</a>,
<a href="https://ec.europa.eu/eurostat/databrowser/view/nrg_pc_204">Eurostat electricity prices</a>.
Built with dbt, DuckDB, Polars & Evidence.</small>
