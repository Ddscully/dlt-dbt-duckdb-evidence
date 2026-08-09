---
title: Seven Findings
description: Seven patterns in the warehouse data on emissions, energy, growth and trade.
---

Seven things that stood out when querying `marts.fct_emissions_energy` and
`analytics.co2_intensity` directly. Every chart below is the query itself, re-run
against the warehouse each time the site is built. For a year-by-year interactive
view of the same tables, see [explore](/). Notes on method are at the
[bottom of the page](#notes-on-method).

Each finding closes with a **So what** box: the decision it feeds, who makes that
decision, and what it costs to get it wrong. An observation nobody acts on isn't
a finding, and three of these are inputs to numbers a company is legally required
to publish.

```sql latest_years
-- The latest year each metric family can populate, computed from coverage
-- rather than assumed. See sources/warehouse/latest_years.sql for why they
-- differ. Every `where year = ...` on this page reads from here.
select * from warehouse.latest_years
```

```sql headline
select
    count(distinct country_iso3) as n_countries,
    cast(cast(max(year) as integer) as varchar) as latest_year,
    sum(co2_mt) filter (
        where year = (select co2_year from ${latest_years})
    ) as world_co2_latest
from warehouse.emissions_energy
where region is not null and co2_mt is not null
```

<Grid cols=3>
    <BigValue data={headline} value=n_countries title="Countries"/>
    <BigValue data={headline} value=latest_year title="Latest CO₂ year"/>
    <BigValue data={headline} value=world_co2_latest fmt="#,##0" title="World CO₂ (Mt)"/>
</Grid>

## 1. Peak emissions arrive in sequence, and some countries haven't reached one

Every large emitter has a year in which its CO₂ output peaked, and sorted by that
year the sequence tracks development.

```sql peaks
with series as (
    select
        country_iso3,
        country_name,
        income_group,
        year,
        co2_mt,
        max(co2_mt) over (partition by country_iso3) as peak_mt
    from warehouse.emissions_energy
    where region is not null
      and co2_mt is not null
),

peaked as (
    select
        country_iso3,
        any_value(country_name) as country_name,
        any_value(income_group) as income_group,
        min(year)    as peak_year,
        max(peak_mt) as peak_mt
    from series
    where co2_mt = peak_mt
    group by country_iso3
),

latest as (
    select country_iso3, co2_mt as mt_latest
    from warehouse.emissions_energy
    where year = (select co2_year from ${latest_years})
)

select
    p.country_name,
    p.income_group,
    p.peak_year,
    p.peak_mt,
    l.mt_latest,
    100 * (l.mt_latest / p.peak_mt - 1) as pct_from_peak,
    case
        when p.peak_year >= (select co2_year from ${latest_years})
            then 'Still rising'
        else 'Past peak'
    end as status
from peaked p
inner join latest l on p.country_iso3 = l.country_iso3
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

Western Europe peaked in the 1970s, the post-Soviet bloc in 1990, the US and
southern Europe in 2005, Japan in 2013. Coloured by World Bank income group,
high-income economies dominate the early decades and upper-middle-income
economies take over from the 2010s on. Bubble size is latest-year emissions. The
UK peaked in 1971 and is 53% below it; France peaked in 1973 and is 51% below.

"Large emitter" here means above 200 Mt in the latest year, roughly the top 30
and about 85% of world emissions. A cluster of mostly lower- and
upper-middle-income Asian and Middle Eastern economies hasn't peaked at all.
Those are left out of the scatter, because their "change since peak" is 0% by
construction (their latest year *is* their peak) and they would all stack on one
point. They get their own chart instead.

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
    xAxisTitle="Latest-year CO₂ (Mt)"
    yAxisTitle="Country"
/>

<DataTable data={peaks} rows=12>
    <Column id=country_name title="Country"/>
    <Column id=peak_year title="Peak" fmt="0"/>
    <Column id=peak_mt title="Peak (Mt)" fmt="#,##0"/>
    <Column id=mt_latest title="Latest (Mt)" fmt="#,##0"/>
    <Column id=pct_from_peak title="vs peak" fmt='0.0"%"' contentType=delta/>
</DataTable>

<Alert status=info>

**So what.** A sourcing country's peak year and its distance from that peak is
its direction of travel, and any supply agreement longer than a few years is a
bet on that direction. The large emitters that haven't peaked at all account for
about **half of world emissions**, so "everywhere is decarbonising" is not a
safe default about the specific country you buy from.

**Who acts:** procurement and site selection. **Cost of getting it wrong:** an
energy- or carbon-linked cost line that rises across the life of a contract
priced on the assumption it would fall.

</Alert>

## 2. Electricity is where the cleanup happened, and coal is most of it

Carbon intensity of electricity is the most legible decarbonisation number there
is: a coal grid runs around 800–900 g of CO₂ per kWh, a gas grid around 400, a
nuclear or hydro grid under 50.

```sql elec_intensity
with base as (
    select
        country_iso3,
        carbon_intensity_elec_g_kwh as g_2005,
        coal_share_elec_pct         as coal_2005
    from warehouse.emissions_energy
    where year = 2005
      and carbon_intensity_elec_g_kwh > 0
),

latest as (
    select
        country_iso3,
        country_name,
        carbon_intensity_elec_g_kwh as g_latest,
        coal_share_elec_pct         as coal_latest,
        low_carbon_share_elec_pct   as low_carbon_latest,
        electricity_generation_twh
    from warehouse.emissions_energy
    where year = (select elec_year from ${latest_years})
      and carbon_intensity_elec_g_kwh is not null
)

select
    l.country_name,
    b.g_2005,
    l.g_latest,
    -- Charted in absolute g/kWh, not percent, on purpose. Norway went from 27
    -- to 30 g and Brazil from 99 to 106, so on a percentage axis those two lead
    -- the "got worse" ranking, ahead of Indonesia adding 29 g to a 651 g grid.
    -- A percentage of an almost-zero denominator is not a comparable quantity.
    l.g_latest - b.g_2005                as g_change,
    100 * (l.g_latest / b.g_2005 - 1)    as pct_change,
    b.coal_2005,
    l.coal_latest,
    l.low_carbon_latest,
    l.electricity_generation_twh,
    case when l.g_latest < b.g_2005 then 'Cleaner per kWh' else 'Dirtier per kWh' end as direction
from latest l
inner join base b on l.country_iso3 = b.country_iso3
-- large grids only: below ~150 TWh a single new plant swings the number
where l.electricity_generation_twh > 150
order by g_change
```

<BarChart
    data={elec_intensity}
    x=country_name
    y=g_change
    series=direction
    seriesColors={{
        'Cleaner per kWh': ['#2a78d6', '#3987e5'],
        'Dirtier per kWh': ['#eb6834', '#d95926']
    }}
    swapXY=true
    sort=false
    yFmt="#,##0"
    xAxisTitle="Change in gCO₂ per kWh since 2005"
    yAxisTitle="Country"
/>

Spain took 329 g out of every kWh (−69%), Poland 324 g and the UK 318 g (−60%).
The mechanism is in the next two columns and it is almost entirely one fuel: the
UK went from 34% coal-fired to 1%, Spain from 27% to 1%, Poland from 91% to 54%.
France barely registers on this chart, which is the measure working. Its grid was
already nuclear at 86 g in 2005 and it still found another 46 g. The countries
that got *dirtier* did the same thing in reverse, Vietnam going from 21% coal to
50% and Indonesia from 39% to 61%, while their absolute generation more than
doubled.

This is also the widest series in the warehouse. `carbon_intensity_elec_g_kwh`
covers about 210 countries against 79 for `renewables_share_pct`, because OWID's
broad-coverage energy series is the electricity mix rather than the
primary-energy mix. Of the 108 countries generating more than 10 TWh, **70% are
cleaner per kWh than they were in 2005.**

Two things the measure does *not* say. A cleaner grid is compatible with rising
total emissions if the grid grows faster than it cleans, which is finding 7. And
electricity is only about a third of energy use: the grid is the part of
decarbonisation that has gone well, not the whole of it.

<DataTable data={elec_intensity} rows=12>
    <Column id=country_name title="Country"/>
    <Column id=g_2005 title="2005 (g/kWh)" fmt="#,##0"/>
    <Column id=g_latest title="Latest (g/kWh)" fmt="#,##0"/>
    <Column id=g_change title="Change (g/kWh)" fmt="#,##0" contentType=delta downIsGood=true/>
    <Column id=pct_change title="Change" fmt='0"%"' contentType=delta downIsGood=true/>
    <Column id=coal_2005 title="Coal 2005 %" fmt="0"/>
    <Column id=coal_latest title="Coal now %" fmt="0"/>
    <Column id=low_carbon_latest title="Low-carbon now %" fmt="0"/>
</DataTable>

<Alert status=info>

**So what.** `carbon_intensity_elec_g_kwh` is not only a climate statistic. It
is the **location-based Scope 2 emission factor**, the number a multi-site
company multiplies its metered kWh by to produce the electricity line in a CSRD,
SECR or CDP disclosure. Across the largest grids in the table above it runs from
Norway at 30 g/kWh to South Africa at 717 g/kWh, a **24× spread**: an identical
100 GWh/year site reports roughly 3 kt CO₂e in one and 72 kt in the other, having
changed nothing but its address.

**Who acts:** sustainability reporting, and site selection long before them.
**Cost of getting it wrong:** a site chosen on power price alone that adds tens
of kilotonnes to a group total nobody re-forecast, and under CSRD an audited one.

</Alert>

## 3. Decoupling is real, on a real-terms basis

Change in emissions against change in inflation-adjusted GDP since 2005.

```sql decoupling
with base_year as (
    select country_iso3, co2_mt, gdp_constant_usd
    from warehouse.emissions_energy
    where year = 2005
),

end_year as (
    select country_iso3, country_name, region, co2_mt, gdp_constant_usd
    from warehouse.emissions_energy
    where year = (select gdp_year from ${latest_years})
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
    xAxisTitle="Real GDP change since 2005 (%)"
    yAxisTitle="CO₂ change since 2005 (%)"
    tooltipTitle=country_name
>
    <ReferenceLine y=0 label="No change in emissions" labelPosition=aboveEnd/>
</ScatterPlot>

Anything in the lower-right quadrant grew its economy while cutting emissions.
The sample is countries emitting more than 100 Mt in 2005, which is large enough
for the comparison to mean something. The US grew 42% in real terms while cutting
emissions 20%; the UK grew 26% and cut 46%.

The standing objection to any chart like this is that production moved offshore,
so the cut is an accounting artifact of where the factory sits. That is a
testable claim, and the next finding tests it.

<Alert status=info>

**So what.** This is the national-scale evidence that "grow and cut" is
achievable, and it is the same choice a company makes when it sets a target: the
US grew 42% in real terms while cutting 20%, the UK grew 26% and cut 46%. An
absolute reduction target is credible alongside a growth plan, but only where
the intensity improvement outruns the growth, and finding 7 shows that isn't
automatic.

**Who acts:** whoever signs the target, which in practice is the CFO rather than
the sustainability team. **Cost of getting it wrong:** committing publicly to an
absolute cut the growth plan makes arithmetically impossible, and restating it
two years later.

</Alert>

## 4. …and it isn't only offshoring

Territorial emissions count what a country burns. Consumption-based emissions
count what it buys: territorial output plus the carbon embodied in imports, minus
the carbon embodied in exports. OWID publishes both, so "you just exported your
emissions" stops being a rhetorical move and becomes a subtraction.

```sql offshoring
with base_year as (
    select country_iso3, co2_mt, consumption_co2
    from warehouse.emissions_energy
    where year = 2005
),

end_year as (
    select country_iso3, country_name, co2_mt, consumption_co2
    from warehouse.emissions_energy
    where year = (select consumption_year from ${latest_years})
)

select
    e.country_name,
    100 * (e.co2_mt / b.co2_mt - 1)                   as territorial_change,
    100 * (e.consumption_co2 / b.consumption_co2 - 1) as consumption_change,
    e.co2_mt
from end_year e
inner join base_year b on e.country_iso3 = b.country_iso3
where b.consumption_co2 is not null
  and e.consumption_co2 is not null
  and e.co2_mt > 250
order by territorial_change
```

```sql offshoring_long
select country_name, 'Territorial (what it burns)' as basis, territorial_change as pct, co2_mt
from ${offshoring}
union all
select country_name, 'Consumption (what it buys)', consumption_change, co2_mt
from ${offshoring}
```

<BarChart
    data={offshoring_long}
    x=country_name
    y=pct
    series=basis
    seriesColors={{
        'Territorial (what it burns)': ['#1baf7a', '#199e70'],
        'Consumption (what it buys)': ['#eda100', '#c98500']
    }}
    type=grouped
    swapXY=true
    sort=false
    yFmt="0"
    xAxisTitle="Country"
    yAxisTitle="Change since 2005 (%)"
/>

The objection is real but partial. The UK's territorial emissions fell 46% and its
consumption emissions fell 36%; Italy 38% against 29%, France 35% against 25%,
Germany 32% against 26%. So roughly a fifth to a third of Europe's headline cut is
trade moving around, and the rest is not. For Japan and the US the two measures
are within a few points of each other, and Canada moves the other way: it cut 4%
territorially and 16% on consumption.

The consumption series covers about 120 countries and runs to
<Value data={latest_years} column=consumption_year_label/>, one year behind the
territorial one.

The same subtraction cuts against the "China is just the world's factory" reading
too: China's consumption emissions have grown *faster* than its territorial ones
since 2005 (+134% against +107%). Its own consumers, not only its export
customers, are behind the increase.

<DataTable data={offshoring} rows=10>
    <Column id=country_name title="Country"/>
    <Column id=territorial_change title="Territorial" fmt='0"%"' contentType=delta downIsGood=true/>
    <Column id=consumption_change title="Consumption" fmt='0"%"' contentType=delta downIsGood=true/>
    <Column id=co2_mt title="Latest (Mt)" fmt="#,##0"/>
</DataTable>

<Alert status=info>

**So what.** Anyone reporting a supply-chain (Scope 3) reduction should expect
the question *did it fall, or did it move?*, and this is the size of that doubt
at national scale: roughly **a fifth to a third** of Europe's headline cut is
trade moving rather than emissions ending. The useful part is that it's a
subtraction, so the question can be answered rather than caveated.

**Who acts:** sustainability reporting and external assurance. **Cost of getting
it wrong:** a claimed reduction that an auditor, or a journalist, reclassifies
as an outsourcing decision.

</Alert>

## 5. Emissions track income, not headcount

```sql income_split
with totals as (
    select
        income_group,
        sum(co2_mt)     as co2_mt,
        sum(population) as population
    from warehouse.emissions_energy
    where year = (select co2_year from ${latest_years})
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

The gap opens at both ends, and the largest block is the middle: upper-middle-income
countries are 38% of the world's people and **half** of its emissions, which is
mostly China. High-income countries hold 17% of the people and 37% of the
emissions. At the other end, low-income countries (around 750 million people, 9%
of humanity) account for 0.6%.

Read this against finding 6 before drawing a conclusion from it: a snapshot of the
current flow is not the same question as who put the carbon there.

```sql income_table
select
    income_group,
    sum(co2_mt)                            as co2_mt,
    sum(population) / 1000000              as population_m,
    sum(co2_mt) * 1000000 / sum(population) as t_per_person
from warehouse.emissions_energy
where year = (select co2_year from ${latest_years})
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

<Alert status=info>

**So what.** Demand for anything that abates carbon, whether equipment,
retrofits or compliance software, sits where the carbon is, and that is not
where the people are. Upper-middle-income countries are 38% of the world's
population and **half** of its emissions; low-income countries are 9% of people
and 0.6%. The two distributions are different enough that they give different
answers to "where should we sell this".

**Who acts:** strategy and market entry. **Cost of getting it wrong:** a
go-to-market plan sized on population, aimed at a segment with almost nothing to
abate.

</Alert>

## 6. Who caused it and who is causing it are different lists

CO₂ accumulates, so a country's share of the *stock* in the atmosphere and its
share of this year's *flow* answer different questions.

```sql stock_vs_flow
select
    country_name,
    share_global_cumulative_co2 as cumulative_share,
    share_global_co2            as current_share,
    cumulative_co2
from warehouse.emissions_energy
where year = (select co2_year from ${latest_years})
  and share_global_cumulative_co2 is not null
order by cumulative_share desc
limit 12
```

```sql stock_vs_flow_long
select country_name, 'Share of all CO₂ ever emitted' as basis, cumulative_share as pct
from ${stock_vs_flow}
union all
select country_name, 'Share of this year''s emissions', current_share
from ${stock_vs_flow}
```

<BarChart
    data={stock_vs_flow_long}
    x=country_name
    y=pct
    series=basis
    seriesColors={{
        'Share of all CO₂ ever emitted': ['#1baf7a', '#199e70'],
        "Share of this year's emissions": ['#eda100', '#c98500']
    }}
    type=grouped
    swapXY=true
    sort=false
    yFmt="0"
    xAxisTitle="Country"
    yAxisTitle="Share of world total (%)"
/>

The two rankings disagree sharply. The stock is the sum of every tonne emitted
since 1750, and the United States has put out roughly a quarter of it while
accounting for about an eighth of current emissions. China is the mirror image:
around 15% of the stock and close to a third of the flow. The UK, the first
industrial economy and 0.8% of emissions today, still carries over 4% of the
cumulative total. That is more than India, which has four times its current
output and twenty times its population.

Neither number tells the whole story on its own. Finding 5 measures the flow;
this measures the stock.

<DataTable data={stock_vs_flow} rows=12>
    <Column id=country_name title="Country"/>
    <Column id=cumulative_co2 title="All CO₂ since 1750 (Mt)" fmt="#,##0"/>
    <Column id=cumulative_share title="Share of stock" fmt='0.0"%"'/>
    <Column id=current_share title="Share of flow" fmt='0.0"%"'/>
</DataTable>

<Alert status=info>

**So what.** Two defensible metrics, one table, opposite rankings: the US leads
on the stock, China on the flow, and each is correct for the question it answers.
Every ranked KPI has this property. The decision isn't which number is right, it's
which definition goes into the target and gets reused: defined once in the
warehouse, not re-derived in each dashboard query by whoever wrote it.

**Who acts:** whoever owns metric definitions. **Cost of getting it wrong:** two
teams presenting different leaders from the same warehouse in the same meeting,
with neither of them wrong.

</Alert>

## 7. Cleaner per dollar, not fewer tonnes

Carbon intensity indexed to 2005, for the six largest emitters.

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

Two things get conflated here and the chart separates them: whether a country's
economy got *cleaner* (CO₂ per dollar of real GDP), and whether its *tonnage*
went up or down. The first is close to universal, and every line falls. China's
carbon intensity is down roughly 47% since 2005 and India's around 13%, while
renewables' share of their energy mix roughly tripled and grew 39% respectively.
Neither country's absolute emissions fell, because GDP grew faster than intensity
dropped. The US, Germany, UK and Japan cut intensity by roughly as much or more
and grew slower, so their tonnage fell too.

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
where i.year = (select gdp_year from ${latest_years})
  and i.country_iso3 in ('CHN', 'IND', 'USA', 'DEU', 'GBR', 'JPN')
order by co2_change_mt desc
```

<DataTable data={intensity_table} rows=6>
    <Column id=country_name title="Country"/>
    <Column id=co2_change_mt title="CO₂ change since 2005 (Mt)" fmt="#,##0" contentType=delta downIsGood=true/>
    <Column id=intensity_change_pct title="Carbon intensity" fmt='0.0"%"' contentType=delta downIsGood=true/>
    <Column id=renewables_change_pct title="Renewables share" fmt='0.0"%"' contentType=delta/>
</DataTable>

<Alert status=info>

**So what.** This is the intensity-target versus absolute-target choice, and the
chart is six countries hitting one while missing the other. China cut carbon
intensity 47% since 2005 and still raised tonnage, because GDP grew faster than
intensity fell. An intensity target is fully compatible with rising emissions,
which is why most corporate target-setting frameworks require an absolute one,
and why an organisation can report a KPI improving every year while its actual
footprint grows.

**Who acts:** whoever sets and reports the target. **Cost of getting it wrong:**
hitting the KPI and missing the outcome, in public, for a decade.

</Alert>

## Notes on method

**Real terms, not nominal.** Anything measured over time divides by
`gdp_constant_usd` (constant 2015 US dollars) rather than `gdp_usd`. Current
dollars move with inflation and exchange rates, which is enough to flip the sign
of a country's apparent progress.

**No year is hardcoded.** Each finding is cut to the latest year its own metrics
can actually populate, read from the data at build time. Those years differ, and
using one number for all of them would quietly gut the samples that run behind.

```sql latest_years_long
select 'CO₂ emissions' as series, co2_year_label as latest_year, 1 as ord from ${latest_years}
union all select 'GDP (constant US$)', gdp_year_label, 2 from ${latest_years}
union all select 'Electricity mix & intensity', elec_year_label, 3 from ${latest_years}
union all select 'Primary energy', energy_year_label, 4 from ${latest_years}
union all select 'Consumption-based CO₂', consumption_year_label, 5 from ${latest_years}
union all select 'EU electricity prices', price_year_label, 6 from ${latest_years}
order by ord
```

<DataTable data={latest_years_long} rows=6 rowNumbers=false>
    <Column id=series title="Series"/>
    <Column id=latest_year title="Latest usable year" align=left/>
</DataTable>

Where a baseline year appears (2005, throughout) it is a deliberate choice of
starting line, not a moving target.

---

<small>Sources: <a href="https://github.com/owid/co2-data">OWID CO₂</a>,
<a href="https://github.com/owid/energy-data">OWID Energy</a>,
<a href="https://databank.worldbank.org/source/world-development-indicators">World Bank WDI</a>.
Coverage caveats: the mart sits on a country-year spine, so a row exists wherever
any source reports and the columns the others don't cover are null, so the charts
above filter for what they need. The narrowest column used here is
<code>consumption_co2</code> (~120 countries); <code>renewables_share_pct</code>
covers 79 and <code>carbon_intensity_elec_g_kwh</code> about 210. 11 small
territories have World Bank data but no OWID emissions.</small>
