---
title: 1. Peak emissions
description: Every large emitter's peak year, how far each has come down from it, and the ones still rising.
sidebar_position: 1
---

[← All nine findings](/findings)

**Rich economies peaked decades ago and are now far below their peaks. The large emitters that have not peaked yet account for about half of world emissions.**

```sql latest_years
select * from warehouse.latest_years
```

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
    title="The earlier a country peaked, the further it has come down"
    subtitle="Large emitters past their peak. Bubble size is latest-year CO₂."
    xAxisTitle="Year emissions peaked"
    yAxisTitle="Change since peak (%)"
    tooltipTitle=country_name
/>

Western Europe peaked in the 1970s, the post-Soviet bloc in 1990, the US and
southern Europe in 2005, Japan in 2013. The UK peaked in 1971 and is 53% below
it; France peaked in 1973 and is 51% below.

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
    title="Still at their peak"
    subtitle="Large emitters whose latest year is their highest, CO₂ (Mt)"
    yAxisTitle="Latest-year CO₂ (Mt)"
/>

These are mostly upper-middle-income Asian and Middle Eastern economies, plus
India. They are charted separately because their "change since peak" is 0% by
construction, and on the scatter they would all stack on one point.

<Alert status=info>

**So what.** A sourcing country's peak year and its distance from that peak is
its direction of travel, and any supply agreement longer than a few years is a
bet on that direction. "Everywhere is decarbonising" is not a safe default about
the specific country you buy from.

**Who acts:** procurement and site selection. **Cost of getting it wrong:** an
energy- or carbon-linked cost line that rises across the life of a contract
priced on the assumption it would fall.

</Alert>

## The data

<DataTable data={peaks} rows=12>
    <Column id=country_name title="Country"/>
    <Column id=peak_year title="Peak" fmt="0"/>
    <Column id=peak_mt title="Peak (Mt)" fmt="#,##0"/>
    <Column id=mt_latest title="Latest (Mt)" fmt="#,##0"/>
    <Column id=pct_from_peak title="vs peak" fmt='0.0"%"' contentType=delta/>
</DataTable>

<Details title="How this is measured">

"Large emitter" means above 200 Mt in the latest year: roughly the top 30
countries and close to 90% of world emissions. A country's peak is the year of
its highest recorded emissions, and "still rising" means that year is the latest
one.

The colours are each country's World Bank income group today, not when it
peaked. Poland peaked in 1987 as a lower-middle-income economy, and the
classification itself only starts that year.

</Details>
