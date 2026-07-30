---
title: Five Findings
description: Five patterns in the warehouse data on emissions, growth and energy.
---

Five things that stood out when querying `marts.fct_emissions_energy` and
`analytics.co2_intensity` directly. Every chart below is the query itself, re-run
against the warehouse each time the site is built. For a year-by-year interactive
view of the same tables, see [explore](/dashboard).

A note on units first. Anything measured over time divides by `gdp_constant_usd`
(constant 2015 US dollars) rather than `gdp_usd`. Current dollars move with
inflation and exchange rates, which is enough to flip the sign of a country's
apparent progress.

```sql headline
select
    count(distinct country_iso3)      as n_countries,
    cast(cast(max(year) as integer) as varchar) as latest_year,
    sum(co2_mt) filter (where year = 2023) as world_co2_2023
from warehouse.emissions_energy
where region is not null and co2_mt is not null
```

<Grid cols=3>
    <BigValue data={headline} value=n_countries title="Countries"/>
    <BigValue data={headline} value=latest_year title="Latest year"/>
    <BigValue data={headline} value=world_co2_2023 fmt="#,##0" title="World CO₂ 2023 (Mt)"/>
</Grid>

## 1. Peak emissions arrive in sequence, and some countries haven't reached one

Every large emitter has a year in which its CO₂ output peaked. Sorted by that year,
the sequence tracks development: Western Europe in the 1970s, the post-Soviet bloc
in 1990, the US and southern Europe in 2005, Japan in 2013. Colored by World Bank
income group, high-income economies dominate the early decades and upper-middle-income
economies take over from the 2010s on. A cluster of mostly lower- and upper-middle-income
Asian and Middle Eastern economies hasn't peaked at all — they're excluded from the
scatter below (their "change since peak" is 0% by construction, since their latest
year *is* their peak, so they'd all stack on one point) and broken out by country in
the bar chart instead.

```sql peaks
with series as (
    select
        country_name,
        income_group,
        year,
        co2_mt,
        max(co2_mt) over (partition by country_name) as peak_mt
    from warehouse.emissions_energy
    where region is not null
      and co2_mt is not null
),

peaked as (
    select
        country_name,
        any_value(income_group) as income_group,
        min(year)    as peak_year,
        max(peak_mt) as peak_mt
    from series
    where co2_mt = peak_mt
    group by country_name
),

latest as (
    select country_name, co2_mt as mt_latest
    from warehouse.emissions_energy
    where year = 2024
)

select
    p.country_name,
    p.income_group,
    p.peak_year,
    p.peak_mt,
    l.mt_latest,
    100 * (l.mt_latest / p.peak_mt - 1) as pct_from_peak,
    case when p.peak_year >= 2024 then 'Still rising' else 'Past peak' end as status
from peaked p
inner join latest l on p.country_name = l.country_name
where l.mt_latest > 200
order by p.peak_year
```

```sql past_peak
select *
from ${peaks}
where status = 'Past peak'
```

<ScatterPlot
    data={past_peak}
    x=peak_year
    y=pct_from_peak
    size=mt_latest
    series=income_group
    seriesColors={{
        'High income': ['#2a78d6', '#3987e5'],
        'Upper middle income': ['#eb6834', '#d95926'],
        'Lower middle income': ['#1baf7a', '#199e70']
    }}
    xFmt="0"
    yFmt="0"
    xAxisTitle="Year emissions peaked"
    yAxisTitle="Change since peak (%)"
    tooltipTitle=country_name
/>

Bubble size is 2024 emissions. The UK peaked in 1971 and is 53% below it. The eight
countries still rising are broken out below, by country.

```sql still_rising
select country_name, mt_latest
from ${peaks}
where status = 'Still rising'
order by mt_latest desc
```

<BarChart
    data={still_rising}
    x=country_name
    y=mt_latest
    swapXY=true
    sort=false
    color="#eb6834"
    labels=true
    labelFmt="#,##0"
    xAxisTitle="2024 CO₂ (Mt)"
    yAxisTitle="Country"
/>

<DataTable data={peaks} rows=12>
    <Column id=country_name title="Country"/>
    <Column id=peak_year title="Peak" fmt="0"/>
    <Column id=peak_mt title="Peak (Mt)" fmt="#,##0"/>
    <Column id=mt_latest title="2024 (Mt)" fmt="#,##0"/>
    <Column id=pct_from_peak title="vs peak" fmt='0.0"%"' contentType=delta/>
</DataTable>

## 2. Energy buys longevity, up to a point

Grouping countries by primary energy consumed per person against life expectancy
produces a clear saturation curve.

```sql energy_life
with base as (
    select
        country_name,
        life_expectancy,
        co2_per_capita,
        1e9 * primary_energy_twh / population as kwh_per_person
    from warehouse.emissions_energy
    where year = 2023
      and primary_energy_twh > 0
      and population > 1000000
      and life_expectancy is not null
)

select
    case
        when kwh_per_person < 5000  then 'under 5k'
        when kwh_per_person < 15000 then '5k – 15k'
        when kwh_per_person < 30000 then '15k – 30k'
        when kwh_per_person < 60000 then '30k – 60k'
        else 'over 60k'
    end as energy_band,
    case
        when kwh_per_person < 5000  then 1
        when kwh_per_person < 15000 then 2
        when kwh_per_person < 30000 then 3
        when kwh_per_person < 60000 then 4
        else 5
    end as band_order,
    count(*)                as n_countries,
    avg(life_expectancy)    as avg_life_expectancy,
    avg(co2_per_capita)     as avg_co2_per_capita
from base
group by energy_band, band_order
order by band_order
```

<BarChart
    data={energy_life}
    x=energy_band
    y=avg_life_expectancy
    sort=false
    xAxisTitle="Primary energy per person (kWh/year)"
    yAxisTitle="Life expectancy (years)"
    labels=true
    labelFmt="0.0"
/>

Moving from the lowest band to 30–60k kWh is worth about 15 years of life. Moving
from there to the top band costs 2.8× the energy and 2.7× the CO₂ per person, and
buys half a year. Past roughly 30k kWh per person, extra energy stops showing up in
life expectancy.

<DataTable data={energy_life} rows=5>
    <Column id=energy_band title="Energy per person"/>
    <Column id=n_countries title="Countries"/>
    <Column id=avg_life_expectancy title="Life expectancy" fmt="0.0"/>
    <Column id=avg_co2_per_capita title="CO₂ / person (t)" fmt="0.0"/>
</DataTable>

## 3. Decoupling is real, on a real-terms basis

Plotting change in emissions against change in inflation-adjusted GDP, 2005–2023.
Anything in the lower-right quadrant grew its economy while cutting emissions.

```sql decoupling
with base_year as (
    select country_iso3, co2_mt, gdp_constant_usd
    from warehouse.emissions_energy
    where year = 2005
),

end_year as (
    select country_iso3, country_name, region, co2_mt, gdp_constant_usd
    from warehouse.emissions_energy
    where year = 2023
)

select
    e.country_name,
    e.region,
    100 * (e.co2_mt / b.co2_mt - 1)                     as co2_change,
    100 * (e.gdp_constant_usd / b.gdp_constant_usd - 1) as real_gdp_change,
    e.co2_mt,
    case
        when e.gdp_constant_usd > b.gdp_constant_usd and e.co2_mt < b.co2_mt
            then 'Cut emissions while growing'
        else 'Did not'
    end as decoupled
from end_year e
inner join base_year b on e.country_iso3 = b.country_iso3
where b.gdp_constant_usd is not null
  and e.gdp_constant_usd is not null
  and b.co2_mt > 100
  and e.co2_mt is not null
```

<ScatterPlot
    data={decoupling}
    x=real_gdp_change
    y=co2_change
    size=co2_mt
    series=decoupled
    seriesColors={{
        'Cut emissions while growing': ['#2a78d6', '#3987e5'],
        'Did not': ['#eb6834', '#d95926']
    }}
    xFmt="0"
    yFmt="0"
    xAxisTitle="Real GDP change 2005–2023 (%)"
    yAxisTitle="CO₂ change 2005–2023 (%)"
    tooltipTitle=country_name
>
    <ReferenceLine y=0 label="No change in emissions" labelPosition=aboveEnd/>
</ScatterPlot>

The US grew 42% in real terms while cutting emissions 20%; the UK grew 26% and cut
46%. One limit of the measure: these are territorial emissions, so production moved
offshore counts against the producing country's ledger, not the consumer's.

## 4. Emissions track income, not headcount

```sql income_split
with totals as (
    select
        income_group,
        sum(co2_mt)     as co2_mt,
        sum(population) as population
    from warehouse.emissions_energy
    where year = 2023
      and income_group is not null
    group by income_group
),

-- the income ladder, not alphabetical or value order
ladder as (
    select 'High income' as income_group, 1 as rung
    union all select 'Upper middle income', 2
    union all select 'Lower middle income', 3
    union all select 'Low income', 4
)

select t.income_group, 'Share of CO₂' as measure,
       100 * t.co2_mt / sum(t.co2_mt) over () as pct, l.rung
from totals t inner join ladder l on t.income_group = l.income_group
union all
select t.income_group, 'Share of population',
       100 * t.population / sum(t.population) over (), l.rung
from totals t inner join ladder l on t.income_group = l.income_group
order by rung, measure
```

<BarChart
    data={income_split}
    x=income_group
    y=pct
    series=measure
    seriesColors={{
        'Share of CO₂': ['#1baf7a', '#199e70'],
        'Share of population': ['#eda100', '#c98500']
    }}
    type=grouped
    swapXY=true
    sort=false
    yFmt="0"
    labels=true
    labelFmt="0"
    xAxisTitle="World Bank income group"
    yAxisTitle="Share of world total (%)"
/>

The two bars diverge at each end of the scale. High-income countries hold 17% of
the world's people and 37% of its emissions. Low-income countries, with 727 million
people, account for 0.6%.

```sql income_table
select
    income_group,
    sum(co2_mt)                            as co2_mt,
    sum(population) / 1000000              as population_m,
    sum(co2_mt) * 1000000 / sum(population) as t_per_person
from warehouse.emissions_energy
where year = 2023
  and income_group is not null
group by income_group
order by t_per_person desc
```

<DataTable data={income_table} rows=4>
    <Column id=income_group title="Income group"/>
    <Column id=co2_mt title="CO₂ (Mt)" fmt="#,##0"/>
    <Column id=population_m title="Population (m)" fmt="#,##0"/>
    <Column id=t_per_person title="t CO₂ / person" fmt="0.00"/>
</DataTable>

## 5. Cleaner per dollar, not fewer tonnes

Finding 3 showed decoupling is real on a real-terms basis. It's worth separating
two things that get conflated: whether a country's economy got *cleaner*
(CO₂ per dollar of real GDP), and whether its *tonnage* went up or down. The
first is close to universal; the second depends on how fast the economy grew
relative to that cleanup. Carbon intensity indexed to 2005, for the six largest
emitters:

```sql intensity_trend
with base as (
    select country_iso3, co2_per_gdp_const_usd as base_intensity
    from warehouse.co2_intensity
    where year = 2005
      and country_iso3 in ('CHN', 'IND', 'USA', 'DEU', 'GBR', 'JPN')
)

select
    i.country_name,
    i.year,
    100 * i.co2_per_gdp_const_usd / b.base_intensity as intensity_index,
    case when i.country_iso3 in ('CHN', 'IND') then 'Tonnage still rising' else 'Tonnage falling' end as tonnage_direction
from warehouse.co2_intensity i
inner join base b on i.country_iso3 = b.country_iso3
where i.year >= 2005
  and i.co2_per_gdp_const_usd is not null
order by i.country_name, i.year
```

<LineChart
    data={intensity_trend}
    x=year
    y=intensity_index
    series=country_name
    seriesColors={{
        'China': ['#eb6834', '#d95926'],
        'India': ['#eda100', '#c98500'],
        'United States': ['#2a78d6', '#3987e5'],
        'Germany': ['#1baf7a', '#199e70'],
        'United Kingdom': ['#8a5fd6', '#7248c4'],
        'Japan': ['#5f9ea0', '#4c8284']
    }}
    yAxisTitle="Carbon intensity, 2005 = 100"
>
    <ReferenceLine y=100 label="2005 level" labelPosition=aboveEnd/>
</LineChart>

Every line falls. China's carbon intensity is down 47% since 2005 and India's is
down 13% — both while renewables' share of their energy mix roughly tripled and
grew 39% respectively. Neither country's absolute emissions fell: China added
6,290 Mt over the same period, India 1,867 Mt, because GDP grew faster than
intensity dropped. The US, Germany, UK and Japan cut intensity by roughly as
much or more and grew slower, so tonnage fell too.

```sql intensity_table
with base as (
    select country_iso3, co2_mt as base_co2, co2_per_gdp_const_usd as base_intensity, renewables_share_pct as base_renew
    from warehouse.co2_intensity
    where year = 2005
)

select
    i.country_name,
    i.co2_mt - b.base_co2 as co2_change_mt,
    100 * (i.co2_per_gdp_const_usd / b.base_intensity - 1) as intensity_change_pct,
    100 * (i.renewables_share_pct / nullif(b.base_renew, 0) - 1) as renewables_change_pct
from warehouse.co2_intensity i
inner join base b on i.country_iso3 = b.country_iso3
where i.year = 2023
  and i.country_iso3 in ('CHN', 'IND', 'USA', 'DEU', 'GBR', 'JPN')
order by co2_change_mt desc
```

<DataTable data={intensity_table} rows=6>
    <Column id=country_name title="Country"/>
    <Column id=co2_change_mt title="CO₂ change, 2005–23 (Mt)" fmt="#,##0" contentType=delta downIsGood=true/>
    <Column id=intensity_change_pct title="Carbon intensity" fmt='0.0"%"' contentType=delta downIsGood=true/>
    <Column id=renewables_change_pct title="Renewables share" fmt='0.0"%"' contentType=delta/>
</DataTable>

One caveat carries over from finding 3: these are territorial emissions, so a
falling intensity number doesn't rule out the cleanup being partly offshored
production rather than a genuinely lower-carbon economy.

---

<small>Sources: <a href="https://github.com/owid/co2-data">OWID CO₂</a>,
<a href="https://github.com/owid/energy-data">OWID Energy</a>,
<a href="https://databank.worldbank.org/source/world-development-indicators">World Bank WDI</a>.
Coverage caveats: the mart sits on a country-year spine, so a row exists wherever
any source reports and the columns the others don't cover are null — the charts
above filter for what they need. <code>renewables_share_pct</code> covers 79
countries (96.7% of world emissions); 11 small territories have World Bank data
but no OWID emissions.</small>
