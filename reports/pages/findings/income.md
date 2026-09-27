---
title: 5. Income
description: Each World Bank income group's share of world population against its share of world CO₂.
sidebar_position: 5
---

[← All nine findings](/findings)

**Upper-middle-income countries are 38% of the world's people and half of its emissions. Low-income countries are 9% of the people and under 1% of the emissions.**

```sql latest_years
select * from warehouse.latest_years
```

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
    title="Share of the world's people and of its CO₂"
    subtitle="By today's World Bank income group, latest year (%)"
/>

The gap opens at both ends, and the largest block is the middle: the
upper-middle-income half of emissions is mostly China. High-income countries hold
17% of the people and 37% of the emissions. Low-income countries, around 750
million people, account for 0.6%.

A snapshot of the current flow is not the same question as who put the carbon
there. [Finding 6](/findings/stock-and-flow) asks that one.

<Alert status=info>

**So what.** Demand for anything that abates carbon, whether equipment, retrofits
or compliance software, sits where the carbon is, and that is not where the
people are. The two distributions are different enough to give different answers
to "where should we sell this".

**Who acts:** strategy and market entry. **Cost of getting it wrong:** a
go-to-market plan sized on population, aimed at a segment with almost nothing to
abate.

</Alert>

## The data

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
