---
title: 6. Stock and flow
description: Each country's share of all CO₂ ever emitted against its share of this year's emissions.
sidebar_position: 6
---

[← All nine findings](/findings)

**The US has emitted about a quarter of all the CO₂ in the atmosphere and emits an eighth of today's. China is the mirror image.**

CO₂ accumulates, so a country's share of the *stock* in the atmosphere and its
share of this year's *flow* answer different questions.

```sql latest_years
select * from warehouse.latest_years
```

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
-- Eight lines, not twelve: the palette has eight colours, and a ninth repeats one.
with top8 as (
    select * from ${stock_vs_flow} order by cumulative_share desc limit 8
)
select country_name, 'All CO₂ ever emitted' as basis, cumulative_share as pct, 1 as ord
from top8
union all
select country_name, 'This year''s emissions', current_share, 2
from top8
order by ord
```

<LineChart
    data={stock_vs_flow_long}
    x=basis
    y=pct
    series=country_name
    sort=false
    markers=true
    yFmt='0"%"'
    title="Share of the stock against share of the flow"
    subtitle="The eight largest contributors to cumulative CO₂ since 1750"
    yAxisTitle="Share of world total"
/>

A line that falls from left to right is a country whose share of today's
emissions is smaller than its historical share. The stock is the sum of every
tonne emitted since 1750.

```sql uk_vs_india
select
    max(share_global_co2) filter (where country_iso3 = 'GBR')            as uk_flow,
    max(share_global_cumulative_co2) filter (where country_iso3 = 'GBR') as uk_stock,
    max(share_global_cumulative_co2) filter (where country_iso3 = 'IND') as india_stock,
    max(co2_mt) filter (where country_iso3 = 'IND')
        / max(co2_mt) filter (where country_iso3 = 'GBR')                as output_multiple,
    max(population) filter (where country_iso3 = 'IND')
        / max(population) filter (where country_iso3 = 'GBR')            as population_multiple
from warehouse.emissions_energy
where year = (select co2_year from ${latest_years})
  and country_iso3 in ('GBR', 'IND')
```

The UK, the first industrial economy and <Value data={uk_vs_india} column=uk_flow fmt='0.0"%"'/> of emissions today, still carries <Value data={uk_vs_india} column=uk_stock fmt='0.0"%"'/> of the cumulative total against <Value data={uk_vs_india} column=india_stock fmt='0.0"%"'/> for India, which emits <Value data={uk_vs_india} column=output_multiple fmt='0'/> times as much every year and has <Value data={uk_vs_india} column=population_multiple fmt='0'/> times as many people.

<Alert status=info>

**So what.** Two defensible metrics, opposite rankings: the US leads on the
stock, China on the flow, and each is correct for the question it answers. Every
ranked KPI has this property. The decision is which definition goes into the
target and gets reused: defined once in the warehouse, not re-derived in each
dashboard query by whoever wrote it.

**Who acts:** whoever owns metric definitions. **Cost of getting it wrong:** two
teams presenting different leaders from the same warehouse in the same meeting,
with neither of them wrong.

</Alert>

## The data

<DataTable data={stock_vs_flow} rows=12>
    <Column id=country_name title="Country"/>
    <Column id=cumulative_co2 title="All CO₂ since 1750 (Mt)" fmt="#,##0"/>
    <Column id=cumulative_share title="Share of stock" fmt='0.0"%"'/>
    <Column id=current_share title="Share of flow" fmt='0.0"%"'/>
</DataTable>
