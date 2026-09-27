---
title: 4. Offshoring
description: Territorial against consumption-based emissions, compared in tonnes rather than percentages.
sidebar_position: 4
---

[← All nine findings](/findings)

**Europe's large economies cut the emissions of what they buy by about as many tonnes as the emissions of what they burn. Their cuts did not simply move abroad.**

Territorial emissions count what a country burns. Consumption-based emissions
count what it buys: territorial output plus the carbon embodied in imports, minus
the carbon embodied in exports. OWID publishes both, so "you just exported your
emissions" becomes a subtraction.

```sql latest_years
select * from warehouse.latest_years
```

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
    b.co2_mt - e.co2_mt                               as territorial_cut_mt,
    b.consumption_co2 - e.consumption_co2             as consumption_cut_mt,
    -- The offshoring test itself, in tonnes. Comparing the two percentages above
    -- is not: a net importer's consumption total starts from a larger base, so an
    -- identical tonnage cut is a smaller percentage of it.
    (e.consumption_co2 - e.co2_mt) - (b.consumption_co2 - b.co2_mt) as net_import_change_mt,
    e.co2_mt
from end_year e
inner join base_year b on e.country_iso3 = b.country_iso3
where b.consumption_co2 is not null
  and e.consumption_co2 is not null
  and e.co2_mt > 250
order by territorial_change
```

```sql cuts_long
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
    data={cuts_long}
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
    title="Emissions cut since 2005, two ways of counting"
    subtitle="Large emitters whose territorial emissions fell, Mt CO₂. Equal bars mean nothing moved abroad."
    yAxisTitle="Cut since 2005 (Mt)"
/>

The UK's consumption emissions fell by 279 Mt against 263 Mt territorially, and
Germany's by 277 Mt against 274 Mt. Japan, the US and Canada all cut more on
consumption than territorially, and Italy and France cut it by 13 Mt and 8 Mt
less, under a tenth of their cuts. Where offshoring does show, it is at the
bottom of the chart: Poland's consumption emissions fell by 22 Mt against 39 Mt
territorially, and Mexico's and Australia's territorial emissions were flat while
their consumption emissions rose by about 50 Mt and 20 Mt.

The same subtraction answers the "China is just the world's factory" reading:
China's consumption emissions rose by about 6,200 Mt since 2005, almost exactly as
much as its territorial ones. Its own economy, not its export customers, accounts
for nearly all of the increase.

<Alert status=warning>

**Why the percentages mislead.** In percentages the objection looks plausible:
the UK's territorial emissions fell 46% and its consumption emissions only 36%.
But each of these countries imports more carbon than it exports, so its
consumption total starts from a larger base and the same tonnage cut is a smaller
percentage of it. The question is answered by a subtraction in tonnes.

</Alert>

<Alert status=info>

**So what.** Anyone reporting a supply-chain (Scope 3) reduction should expect
the question *did it fall, or did it move?* For Europe's largest economies it
fell: the carbon they import, net, barely changed while their territorial
emissions dropped by a third or more.

**Who acts:** sustainability reporting and external assurance. **Cost of getting
it wrong:** a claimed reduction that an auditor, or a journalist, reclassifies
as an outsourcing decision.

</Alert>

## The data

The net-import column is the change since 2005 in the carbon each country imports, net.

<DataTable data={offshoring} rows=10>
    <Column id=country_name title="Country"/>
    <Column id=territorial_change title="Territorial" fmt='0"%"' contentType=delta downIsGood=true/>
    <Column id=consumption_change title="Consumption" fmt='0"%"' contentType=delta downIsGood=true/>
    <Column id=net_import_change_mt title="Net imported CO₂, change (Mt)" fmt="#,##0" contentType=delta downIsGood=true/>
    <Column id=co2_mt title="Latest (Mt)" fmt="#,##0"/>
</DataTable>

The consumption series covers about 120 countries and runs one year behind the territorial one, to <Value data={latest_years} column=consumption_year_label/> at the latest.
