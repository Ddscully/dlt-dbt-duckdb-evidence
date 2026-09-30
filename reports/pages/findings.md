---
title: Nine Findings
description: Nine patterns in the warehouse data on emissions, energy, growth and trade.
sidebar_position: 6
---

What six decades of national emissions, energy and economic data support, one
chart each. Every chart is a live query, re-run each time the site is built;
follow a finding's link for the full argument, the data behind it and the
decision it feeds.

```sql latest_years
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

## 1. Rich countries peaked decades ago. China and India have not.

```sql peak_index
-- Each country against its own record year, so a 12,000 Mt emitter and a 300 Mt
-- one share an axis. 100 means "at its peak".
with series as (
    select
        country_name,
        year,
        co2_mt,
        max(co2_mt) over (partition by country_iso3) as peak_mt
    from warehouse.emissions_energy
    where country_iso3 in ('GBR', 'DEU', 'USA', 'JPN', 'CHN', 'IND')
      and co2_mt is not null
)

select country_name, year, 100 * co2_mt / peak_mt as pct_of_peak
from series
where year >= 1950
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
        'Germany': ['#1baf7a', '#199e70'],
        'United Kingdom': ['#8a5fd6', '#7248c4'],
        'Japan': ['#5f9ea0', '#4c8284']
    }}
    xFmt="0"
    yFmt="0"
    yMax=100
    echartsOptions={{xAxis: {min: 'dataMin', max: 'dataMax'}}}
    title="Emissions as % of each country's peak"
    yAxisTitle="% of peak year"
/>

The UK peaked in 1971, Germany in 1979, the US in 2005 and Japan in 2013; each
now emits well below its record. China and India are still at theirs, and the
large emitters that have not peaked yet add up to about half of world emissions.
**[Peak emissions →](/findings/peak-emissions)**

## 2. Grids got cleaner, and it was mostly coal

```sql elec_change
with base as (
    select country_iso3, carbon_intensity_elec_g_kwh as g_2005
    from warehouse.emissions_energy
    where year = 2005
      and carbon_intensity_elec_g_kwh > 0
),

changes as (
    select
        l.country_name,
        l.carbon_intensity_elec_g_kwh - b.g_2005 as g_change,
        case when l.carbon_intensity_elec_g_kwh < b.g_2005 then 'Cleaner per kWh' else 'Dirtier per kWh' end as direction
    from warehouse.emissions_energy l
    inner join base b on l.country_iso3 = b.country_iso3
    where l.year = (select elec_year from ${latest_years})
      and l.carbon_intensity_elec_g_kwh is not null
      -- large grids only: below ~150 TWh a single new plant swings the number
      and l.electricity_generation_twh > 150
)

-- The ten largest improvements and every grid that got dirtier; the finding's
-- page has all of them.
select * from changes
qualify row_number() over (order by g_change) <= 10 or g_change > 0
order by g_change
```

<BarChart
    data={elec_change}
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
    title="Grid carbon intensity since 2005"
    subtitle="Change in gCO₂e per kWh: the ten largest improvements among grids over 150 TWh, and every one that got dirtier"
/>

Spain, Poland and the UK each took more than 300 g out of every kWh, by closing
coal plants. Vietnam and Indonesia went the other way, building coal while their
demand doubled. **[Electricity →](/findings/electricity)**

## 3. Economies can grow while emissions fall

```sql decoupling
with base_year as (
    select country_iso3, co2_mt, gdp_constant_usd
    from warehouse.emissions_energy
    where year = 2005
)

select
    e.country_name,
    100 * (e.co2_mt / b.co2_mt - 1)                     as co2_change,
    100 * (e.gdp_constant_usd / b.gdp_constant_usd - 1) as real_gdp_change,
    e.co2_mt,
    case
        when e.gdp_constant_usd > b.gdp_constant_usd and e.co2_mt < b.co2_mt
            then 'Cut emissions while growing'
        else 'Did not'
    end as decoupled
from warehouse.emissions_energy e
inner join base_year b on e.country_iso3 = b.country_iso3
where e.year = (select gdp_year from ${latest_years})
  and b.gdp_constant_usd is not null
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
    title="Growth against emissions since 2005"
    subtitle="Countries emitting over 100 Mt in 2005"
    xAxisTitle="Real GDP change (%)"
    yAxisTitle="CO₂ change (%)"
    tooltipTitle=country_name
>
    <ReferenceLine y=0 label="No change in emissions" labelPosition=aboveEnd/>
</ScatterPlot>

Every blue point grew its inflation-adjusted economy while cutting emissions,
the US and the UK among them. **[Decoupling →](/findings/decoupling)**

## 4. The cuts were real, not moved abroad

```sql offshoring_cuts
with base_year as (
    select country_iso3, co2_mt, consumption_co2
    from warehouse.emissions_energy
    where year = 2005
),

cuts as (
    select
        e.country_name,
        b.co2_mt - e.co2_mt                   as territorial_cut_mt,
        b.consumption_co2 - e.consumption_co2 as consumption_cut_mt
    from warehouse.emissions_energy e
    inner join base_year b on e.country_iso3 = b.country_iso3
    where e.year = (select consumption_year from ${latest_years})
      and b.consumption_co2 is not null
      and e.consumption_co2 is not null
      and e.co2_mt > 250
      and b.co2_mt > e.co2_mt
)

select country_name, 'What it burns (territorial)' as basis, territorial_cut_mt as cut_mt, territorial_cut_mt as ord from cuts
union all
select country_name, 'What it buys (consumption)', consumption_cut_mt, territorial_cut_mt from cuts
order by ord desc
```

<BarChart
    data={offshoring_cuts}
    x=country_name
    y=cut_mt
    series=basis
    seriesColors={{
        'What it burns (territorial)': ['#1baf7a', '#199e70'],
        'What it buys (consumption)': ['#eda100', '#c98500']
    }}
    type=grouped
    swapXY=true
    sort=false
    yFmt="#,##0"
    title="Emissions cut since 2005, two ways"
    subtitle="Mt CO₂, large emitters whose territorial emissions fell"
/>

If rich countries had only moved their factories abroad, the emissions of what
they *buy* would have fallen much less than the emissions of what they *burn*.
For the UK, Germany, the US and Japan the two cuts are about the same size.
**[Offshoring →](/findings/offshoring)**

## 5. Emissions follow income, not population

```sql income_mix
with totals as (
    select
        income_group,
        sum(co2_mt)     as co2_mt,
        sum(population) as population
    from warehouse.emissions_energy
    where year = (select co2_year from ${latest_years})
      and income_group is not null
    group by income_group
)

select 'Share of CO₂' as measure, income_group, 100 * co2_mt / sum(co2_mt) over () as pct from totals
union all
select 'Share of people', income_group, 100 * population / sum(population) over () from totals
```

<BarChart
    data={income_mix}
    x=measure
    y=pct
    series=income_group
    seriesOrder={['High income', 'Upper middle income', 'Lower middle income', 'Low income']}
    seriesColors={{
        'High income': ['#2a78d6', '#3987e5'],
        'Upper middle income': ['#eda100', '#c98500'],
        'Lower middle income': ['#e87ba4', '#d55181'],
        'Low income': ['#008300', '#008300']
    }}
    type=stacked100
    swapXY=true
    sort=false
    labels=true
    labelFmt="0%"
    chartAreaHeight=140
    title="People and CO₂ by income group"
/>

Low-income countries are 9% of the world's people and under 1% of its
emissions. Upper-middle-income countries, mostly China, are 38% of the people
and half the emissions. **[Income →](/findings/income)**

## 6. Who caused it is not who is causing it

```sql stock_flow
with top8 as (
    select country_name, share_global_cumulative_co2, share_global_co2
    from warehouse.emissions_energy
    where year = (select co2_year from ${latest_years})
      and share_global_cumulative_co2 is not null
    order by share_global_cumulative_co2 desc
    limit 8
)

select country_name, 'All CO₂ ever emitted' as basis, share_global_cumulative_co2 as pct, 1 as ord from top8
union all
select country_name, 'This year''s emissions', share_global_co2, 2 from top8
order by ord
```

<LineChart
    data={stock_flow}
    x=basis
    y=pct
    series=country_name
    sort=false
    markers=true
    yFmt='0"%"'
    title="Share of all CO₂ ever against this year's"
    subtitle="The eight largest contributors since 1750"
/>

The US has put out about a quarter of all the CO₂ ever emitted, and emits an
eighth of today's. China is the mirror image. Both rankings are correct; they
answer different questions. **[Stock and flow →](/findings/stock-and-flow)**

## 7. Cleaner per dollar is not fewer tonnes

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
    100 * i.co2_per_gdp_const_usd / b.base_intensity as intensity_index
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
    xFmt="0"
    yFmt="0"
    title="CO₂ per dollar of real GDP, 2005 = 100"
    yAxisTitle="Carbon intensity index"
>
    <ReferenceLine y=100 label="2005 level" labelPosition=aboveEnd/>
</LineChart>

Every line falls: all six economies emit less per dollar than in 2005. China's
and India's tonnage still rose, because their economies grew faster than their
intensity fell. **[Intensity →](/findings/intensity)**

## 8. The gap between grids is not closing

```sql grid_percentiles
-- The same countries every year from 2000, grids above 10 TWh (see the finding's page).
with eligible as (
    select country_iso3
    from warehouse.emissions_energy
    where year between 2000 and (select elec_year from ${latest_years})
      and carbon_intensity_elec_g_kwh is not null
      and electricity_generation_twh > 10
    group by 1
    having count(*) = (select elec_year from ${latest_years}) - 1999
),

stats as (
    select
        year,
        quantile_cont(carbon_intensity_elec_g_kwh, 0.1) as p10,
        quantile_cont(carbon_intensity_elec_g_kwh, 0.5) as p50,
        quantile_cont(carbon_intensity_elec_g_kwh, 0.9) as p90,
        count(*)                                        as n_countries
    from warehouse.emissions_energy
    where country_iso3 in (select country_iso3 from eligible)
      and year between 2000 and (select elec_year from ${latest_years})
    group by year
)

select year, '90th percentile (dirtier grids)' as grid, p90 as g_kwh, n_countries from stats
union all select year, 'Median', p50, n_countries from stats
union all select year, '10th percentile (cleaner grids)', p10, n_countries from stats
order by year
```

<LineChart
    data={grid_percentiles}
    x=year
    y=g_kwh
    series=grid
    seriesColors={{
        '90th percentile (dirtier grids)': ['#eb6834', '#d95926'],
        'Median': ['#8a8f98', '#9aa0a8'],
        '10th percentile (cleaner grids)': ['#2a78d6', '#3987e5']
    }}
    xFmt="0"
    yFmt="#,##0"
    yMin=0
    title="Spread of national grids"
    subtitle="gCO₂e per kWh, 10th, 50th and 90th percentiles of the same countries each year"
/>

The same <Value data={grid_percentiles} column=n_countries/> countries in every year, so the sample cannot move the lines.

The typical grid is cleaner, but the lines fall in parallel, so a site on a dirty
grid carries much the same penalty as it did in 2000. **[Grid gap →](/findings/grid-gap)**

## 9. The rich world's cut was coal; what is left is oil and gas

```sql fuel_mix
-- Today's high-income economies publishing every fuel line in every year since 1990.
with members as (
    select country_iso3
    from warehouse.emissions_energy
    where income_group = 'High income'
      and year between 1990 and (select co2_year from ${latest_years})
      and co2_mt is not null
      and coal_co2 is not null
      and oil_co2 is not null
      and gas_co2 is not null
    group by country_iso3
    having count(*) = (select co2_year from ${latest_years}) - 1989
),

panel as (
    select
        year,
        sum(coal_co2)                              as coal_mt,
        sum(oil_co2)                               as oil_mt,
        sum(gas_co2)                               as gas_mt,
        sum(co2_mt - coal_co2 - oil_co2 - gas_co2) as other_mt
    from warehouse.emissions_energy
    where country_iso3 in (select country_iso3 from members)
      and year between 1990 and (select co2_year from ${latest_years})
    group by year
)

select year, 'Coal' as fuel, coal_mt as co2_mt from panel
union all select year, 'Oil', oil_mt from panel
union all select year, 'Gas', gas_mt from panel
union all select year, 'Cement, flaring and other', other_mt from panel
order by year
```

<AreaChart
    data={fuel_mix}
    x=year
    y=co2_mt
    series=fuel
    seriesColors={{
        'Coal': ['#eb6834', '#d95926'],
        'Oil': ['#2a78d6', '#3987e5'],
        'Gas': ['#1baf7a', '#199e70'],
        'Cement, flaring and other': ['#eda100', '#c98500']
    }}
    xFmt="0"
    yFmt="#,##0"
    title="Rich-world CO₂ by fuel (Mt)"
/>

Since 2005 coal has fallen by about as much as the whole net cut, and gas has
grown. What remains is mostly oil and gas, in vehicles, boilers and furnaces,
which are slower to replace than power stations. **[Fuels →](/findings/fuels)**

---

<Details title="Notes on method">

**Real terms, not nominal.** Anything measured over time divides by
`gdp_constant_usd` (constant 2015 US dollars) rather than `gdp_usd`. Current
dollars move with inflation and exchange rates, which is enough to flip the sign
of a country's apparent progress.

**No year is hardcoded.** Each finding is cut to the latest year its own metrics
can populate, read from the data at build time. Where a baseline year appears
(2005, throughout) it is a fixed starting line, chosen once and not moved to suit
a chart. The captions quote some figures that are not computed, so after a data
release a sentence can trail its chart by a point or two; the chart is the
current number.

```sql latest_years_long
select 'CO₂ emissions' as series, co2_year_label as latest_year, 1 as ord from ${latest_years}
union all select 'GDP (constant US$)', gdp_year_label, 2 from ${latest_years}
union all select 'Electricity mix & intensity', elec_year_label, 3 from ${latest_years}
union all select 'Primary energy', energy_year_label, 4 from ${latest_years}
union all select 'Consumption-based CO₂', consumption_year_label, 5 from ${latest_years}
union all select 'Eurostat electricity prices', price_year_label, 6 from ${latest_years}
order by ord
```

<DataTable data={latest_years_long} rows=6 rowNumbers=false>
    <Column id=series title="Series"/>
    <Column id=latest_year title="Latest usable year" align=left/>
</DataTable>

**Coverage.** A row exists wherever any source reports, with nulls in the columns
the others don't cover, so each chart filters for what it needs. The narrowest
column used is `consumption_co2` (about 120 countries); `renewables_share_pct`
covers 79 and `carbon_intensity_elec_g_kwh` about 210. The
[Coverage](/coverage) page has the detail.

</Details>

<small>Sources: <a href="https://github.com/owid/co2-data">OWID CO₂</a>,
<a href="https://github.com/owid/energy-data">OWID Energy</a>,
<a href="https://databank.worldbank.org/source/world-development-indicators">World Bank WDI</a>.</small>

For a year-by-year view of the same data, use the [Country Explorer](/countries).
