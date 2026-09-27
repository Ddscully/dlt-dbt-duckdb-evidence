---
title: Income groups
description: Why the carbon intensity of each income group is computed with each country in the group it held that year, weighted by output.
sidebar_position: 1
---

[← Country Explorer](/countries)

**Two choices sit behind every income-group trend: which year's classification to use, and whether to average countries or divide totals. Made the other way, this chart told a different story.**

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
