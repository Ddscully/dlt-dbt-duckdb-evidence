---
title: 2. Electricity
description: How much carbon each large grid took out of a kWh since 2005, and how much of it was coal.
sidebar_position: 2
---

[← All nine findings](/findings)

**Most large grids emit less per kWh than they did in 2005, and the biggest improvements came almost entirely from burning less coal.**

A coal grid runs around 800–900 g of CO₂ per kWh, a modern gas grid around 400,
and a nuclear or hydro grid under 50.

```sql latest_years
select * from warehouse.latest_years
```

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
    title="Change in grid carbon intensity since 2005"
    subtitle="Grids generating more than 150 TWh a year, gCO₂ per kWh"
    yAxisTitle="Change in gCO₂ per kWh"
/>

Spain took 329 g out of every kWh (−69%), Poland 324 g and the UK 319 g (−60%).
The mechanism is almost entirely one fuel: the UK went from 34% coal-fired to 1%,
Spain from 27% to 1% and Poland from 91% to 54%. The countries that got *dirtier*
did the same in reverse: Vietnam went from 21% coal to 50% and Indonesia from 39%
to 61%, while their generation more than doubled.

France barely registers, which is the measure working: its grid was already
nuclear at 86 g in 2005, and it still found another 45 g.

Of the 108 countries generating more than 10 TWh, **70% are cleaner per kWh than
they were in 2005.** But a cleaner grid is compatible with rising total emissions
if the grid grows faster than it cleans ([finding 7](/findings/intensity)), and
electricity is only about a third of energy use.

<Alert status=info>

**So what.** Grid carbon intensity is the **location-based Scope 2 emission
factor**, the number a multi-site company multiplies its metered kWh by to produce
the electricity line in a CSRD, SECR or CDP disclosure. Across the largest grids
it runs from Norway at 30 g/kWh to South Africa at 717 g/kWh, a **24× spread**:
an identical 100 GWh/year site reports roughly 3 kt CO₂e in one and 72 kt in the
other, having changed nothing but its address.

**Who acts:** sustainability reporting, and site selection long before them.
**Cost of getting it wrong:** a site chosen on power price alone that adds tens
of kilotonnes to a group total nobody re-forecast, and under CSRD an audited one.

</Alert>

## The data

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

<Details title="How this is measured">

The change is charted in g/kWh rather than percent. On a percentage axis Norway
(27 to 30 g) and Brazil (99 to 106 g) would lead the "got worse" ranking, ahead
of Indonesia adding 29 g to a 651 g grid.

Grid carbon intensity is the widest series in the warehouse, covering about 210
countries against 79 for renewables' share of all energy, because OWID's
broad-coverage energy series is the electricity mix rather than the
primary-energy mix. Grids under 150 TWh are left off the chart, because a single
new plant swings their number.

</Details>
