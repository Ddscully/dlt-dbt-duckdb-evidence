---
title: 7. Intensity
description: Carbon intensity of the six largest emitters' economies against what happened to their tonnage.
sidebar_position: 7
---

[← All nine findings](/findings)

**Every large economy now emits less CO₂ per dollar than in 2005. China and India still emit more tonnes, because their economies grew faster than their intensity fell.**

```sql latest_years
select * from warehouse.latest_years
```

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
    subtitle="The six largest emitters"
    yAxisTitle="Carbon intensity index"
>
    <ReferenceLine y=100 label="2005 level" labelPosition=aboveEnd/>
</LineChart>

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
    i.renewables_share_pct - b.base_renew as renewables_change_pp,
    case when i.co2_mt > b.base_co2 then 'Tonnage rose' else 'Tonnage fell' end as direction
from warehouse.co2_intensity i
inner join base b on i.country_iso3 = b.country_iso3
where i.year = (select gdp_year from ${latest_years})
  and i.country_iso3 in ('CHN', 'IND', 'USA', 'DEU', 'GBR', 'JPN')
order by co2_change_mt desc
```

<BarChart
    data={intensity_table}
    x=country_name
    y=co2_change_mt
    series=direction
    seriesColors={{
        'Tonnage fell': ['#2a78d6', '#3987e5'],
        'Tonnage rose': ['#eb6834', '#d95926']
    }}
    swapXY=true
    sort=false
    labels=true
    labelFmt="#,##0"
    title="…but tonnage is a different question"
    subtitle="Change in CO₂ since 2005 (Mt)"
    yAxisTitle="Change in CO₂ (Mt)"
/>

Two things get conflated here and the charts separate them: whether an economy
got *cleaner* (CO₂ per dollar of real GDP), and whether its *tonnage* went up or
down. The first is close to universal. The second depends on how fast the
economy grew.

```sql intensity_examples
with base as (
    select country_iso3, co2_per_gdp_const_usd as base_intensity, renewables_share_pct as base_renew
    from warehouse.co2_intensity
    where year = 2005
      and country_iso3 in ('CHN', 'IND', 'JPN')
),

latest as (
    select country_iso3, co2_per_gdp_const_usd as intensity, renewables_share_pct as renew
    from warehouse.co2_intensity
    where year = (select gdp_year from ${latest_years})
      and country_iso3 in ('CHN', 'IND', 'JPN')
),

changes as (
    select
        l.country_iso3,
        100 * (1 - l.intensity / b.base_intensity) as intensity_cut,
        b.base_renew,
        l.renew
    from latest l
    inner join base b on l.country_iso3 = b.country_iso3
)

select
    max(intensity_cut) filter (where country_iso3 = 'CHN') as china_cut,
    max(intensity_cut) filter (where country_iso3 = 'IND') as india_cut,
    max(intensity_cut) filter (where country_iso3 = 'JPN') as japan_cut,
    max(base_renew) filter (where country_iso3 = 'CHN')    as china_renew_2005,
    max(renew) filter (where country_iso3 = 'CHN')         as china_renew_latest,
    max(base_renew) filter (where country_iso3 = 'IND')    as india_renew_2005,
    max(renew) filter (where country_iso3 = 'IND')         as india_renew_latest
from changes
```

China cut the carbon intensity of its economy by <Value data={intensity_examples} column=china_cut fmt='0"%"'/> between 2005 and <Value data={latest_years} column=gdp_year_label/> and India by <Value data={intensity_examples} column=india_cut fmt='0"%"'/> in the same years, while renewables went from <Value data={intensity_examples} column=china_renew_2005 fmt='0.0"%"'/> to <Value data={intensity_examples} column=china_renew_latest fmt='0.0"%"'/> of China's energy mix and from <Value data={intensity_examples} column=india_renew_2005 fmt='0.0"%"'/> to <Value data={intensity_examples} column=india_renew_latest fmt='0.0"%"'/> of India's.

The US, Germany and the UK cut intensity by about as much or more and grew more
slowly, so their tonnage fell.

Japan got there the other way round: it cut intensity by only <Value data={intensity_examples} column=japan_cut fmt='0"%"'/> and its tonnage fell anyway, because its economy barely grew.

<Alert status=info>

**So what.** This is the intensity-target versus absolute-target choice, and the
charts show two countries hitting one while missing the other. An intensity
target is fully compatible with rising emissions, which is why most corporate
target-setting frameworks require an absolute one, and why an organisation can
report a KPI improving every year while its actual footprint grows.

**Who acts:** whoever sets and reports the target. **Cost of getting it wrong:**
hitting the KPI and missing the outcome, in public, for a decade.

</Alert>

## The data

<DataTable data={intensity_table} rows=6>
    <Column id=country_name title="Country"/>
    <Column id=co2_change_mt title="CO₂ change since 2005 (Mt)" fmt="#,##0" contentType=delta downIsGood=true/>
    <Column id=intensity_change_pct title="Carbon intensity change" fmt='0.0"%"' contentType=delta downIsGood=true/>
    <Column id=renewables_change_pp title="Renewables share change (pp)" fmt='0.0" pp"' contentType=delta/>
</DataTable>
