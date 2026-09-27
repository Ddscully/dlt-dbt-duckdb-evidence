---
title: 9. Fuels
description: The CO₂ of today's high-income economies by fuel since 1990, and what is left once the coal is gone.
sidebar_position: 9
---

[← All nine findings](/findings)

```sql latest_years
select * from warehouse.latest_years
```

```sql fuel_panel
-- Today's high-income economies, held fixed: the same countries in every year, so
-- a line moves only because emissions did. Today's classification is the right
-- one here — "the economies that are rich now" is a fixed list, which is what a
-- before-and-after comparison needs — unlike an income-group trend, where each
-- year wants the group as it stood (see the country explorer).
--
-- Only countries publishing CO₂ and all three fuel lines in every year from 1990,
-- so no line jumps when a small economy starts reporting one.
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
)

select
    year,
    count(*)                                    as n_countries,
    sum(coal_co2)                               as coal_mt,
    sum(oil_co2)                                as oil_mt,
    sum(gas_co2)                                as gas_mt,
    -- Cement, flaring and other industry: what OWID's total carries beyond the three fuels.
    sum(co2_mt - coal_co2 - oil_co2 - gas_co2)  as other_mt,
    sum(co2_mt)                                 as total_mt
from warehouse.emissions_energy
where country_iso3 in (select country_iso3 from members)
  and year between 1990 and (select co2_year from ${latest_years})
group by year
order by year
```

```sql fuel_panel_long
select year, 'Coal' as fuel, coal_mt as co2_mt from ${fuel_panel}
union all select year, 'Oil', oil_mt from ${fuel_panel}
union all select year, 'Gas', gas_mt from ${fuel_panel}
union all select year, 'Cement, flaring and other', other_mt from ${fuel_panel}
order by year
```

```sql fuel_change
with first_year as (
    select * from ${fuel_panel} where year = 2005
),

last_year as (
    select * from ${fuel_panel} where year = (select co2_year from ${latest_years})
)

select
    l.n_countries,
    f.total_mt - l.total_mt                          as total_cut_mt,
    -- No "coal's share of the cut": gas rose, so the net cut is smaller than coal's
    -- fall and the share prints over 100%. The two tonnages side by side say it.
    f.coal_mt - l.coal_mt                            as coal_cut_mt,
    f.oil_mt - l.oil_mt                              as oil_cut_mt,
    l.gas_mt - f.gas_mt                              as gas_rise_mt,
    100 * f.oil_mt / f.total_mt                      as oil_share_2005,
    100 * l.oil_mt / l.total_mt                      as oil_share_latest,
    100 * f.gas_mt / f.total_mt                      as gas_share_2005,
    100 * l.gas_mt / l.total_mt                      as gas_share_latest,
    100 * (l.oil_mt + l.gas_mt) / l.total_mt         as oil_gas_share_latest
from first_year f
cross join last_year l
```

Between 2005 and <Value data={latest_years} column=co2_year_label/> the CO₂ of today's high-income economies fell by <Value data={fuel_change} column=total_cut_mt fmt="#,##0"/> Mt. Coal alone fell by about as much, <Value data={fuel_change} column=coal_cut_mt fmt="#,##0"/> Mt, while gas rose.

<AreaChart
    data={fuel_panel_long}
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
    title="CO₂ of today's high-income economies, by fuel"
    subtitle="The same countries every year since 1990, Mt CO₂"
    yAxisTitle="CO₂ (Mt)"
/>

Over the same years oil fell by <Value data={fuel_change} column=oil_cut_mt fmt="#,##0"/> Mt and gas rose by <Value data={fuel_change} column=gas_rise_mt fmt="#,##0"/> Mt. Gas went from <Value data={fuel_change} column=gas_share_2005 fmt='0"%"'/> to <Value data={fuel_change} column=gas_share_latest fmt='0"%"'/> of what the group emits and oil from <Value data={fuel_change} column=oil_share_2005 fmt='0"%"'/> to <Value data={fuel_change} column=oil_share_latest fmt='0"%"'/> of it, so oil and gas together are now <Value data={fuel_change} column=oil_gas_share_latest fmt='0"%"'/> of what is left.

<Alert status=info>

**So what.** Twenty years of rich-world decarbonisation is, net, the story of
burning less coal, and part of the coal was replaced by gas. What remains is
mostly oil, much of it transport, and gas, much of it heating and industry. Those
are cut by replacing vehicles, boilers and furnaces one at a time, not by closing
a few hundred power stations, so a reduction rate measured over the coal years is
a poor guide to the next twenty.

**Who acts:** whoever builds a decarbonisation roadmap, or prices carbon
exposure, off a historical trend. **Cost of getting it wrong:** a trajectory
that holds while there is coal to close and stalls when it runs out, with the
target still set on the old slope.

</Alert>

## Country by country

```sql fuel_by_country
with first_year as (
    select country_iso3, co2_mt, coal_co2, oil_co2, gas_co2
    from warehouse.emissions_energy
    where year = 2005
),

last_year as (
    select country_iso3, country_name, co2_mt, coal_co2, oil_co2, gas_co2
    from warehouse.emissions_energy
    where year = (select co2_year from ${latest_years})
)

select
    l.country_name,
    l.co2_mt - f.co2_mt                 as total_change,
    l.coal_co2 - f.coal_co2             as coal_change,
    l.oil_co2 - f.oil_co2               as oil_change,
    l.gas_co2 - f.gas_co2               as gas_change,
    100 * l.coal_co2 / l.co2_mt         as coal_share_latest,
    100 * l.oil_co2 / l.co2_mt          as oil_share_latest
from last_year l
inner join first_year f on l.country_iso3 = f.country_iso3
-- Large emitters whose total fell: the countries a "look how far we've come"
-- trajectory is drawn from.
where f.co2_mt > 250
  and l.co2_mt < f.co2_mt
  and l.coal_co2 is not null
  and l.oil_co2 is not null
  and l.gas_co2 is not null
  and f.coal_co2 is not null
  and f.oil_co2 is not null
  and f.gas_co2 is not null
order by total_change
```

The pattern holds for the large emitters whose totals fell. The US cut more coal
than its whole net fall, and gas took back part of it. Across most of the table
oil is now the largest line and coal one of the smallest. Japan is the outlier:
its fall came mostly from oil, because after 2011 its grid leaned on coal and gas
to replace nuclear.

<DataTable data={fuel_by_country} rows=12>
    <Column id=country_name title="Country"/>
    <Column id=total_change title="All CO₂ since 2005 (Mt)" fmt="#,##0" contentType=delta downIsGood=true/>
    <Column id=coal_change title="Coal (Mt)" fmt="#,##0" contentType=delta downIsGood=true/>
    <Column id=oil_change title="Oil (Mt)" fmt="#,##0" contentType=delta downIsGood=true/>
    <Column id=gas_change title="Gas (Mt)" fmt="#,##0" contentType=delta downIsGood=true/>
    <Column id=coal_share_latest title="Coal, share now" fmt='0"%"'/>
    <Column id=oil_share_latest title="Oil, share now" fmt='0"%"'/>
</DataTable>

The panel is the <Value data={fuel_change} column=n_countries/> high-income economies that publish every fuel line in every year since 1990, which between them are almost all of the group's emissions.
