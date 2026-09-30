---
title: Half-year prices
description: Eurostat's household electricity prices by half-year, what averaging them into an annual figure hides, and which of two averages is the EU's.
sidebar_position: 2
---

[← Country Explorer](/countries)

**Eurostat publishes household prices twice a year. Averaged into an annual figure, the 2022 spikes become a smooth rise, and the Netherlands gets a price no household paid.**

```sql volatile_countries
-- The countries with the largest single half-over-half move in cents, not
-- percent: a percent ranking promotes small markets moving off a low base.
select country_name
from warehouse.eu_electricity_prices_semiannual
group by country_name
order by max(abs(change_vs_previous_half_eur_kwh)) desc nulls last
limit 6
```

```sql semiannual_prices
select
    period_start_date,
    country_name,
    electricity_price_eur_kwh
from warehouse.eu_electricity_prices_semiannual
where country_name in (select country_name from ${volatile_countries})
order by period_start_date
```

<LineChart
    data={semiannual_prices}
    x=period_start_date
    y=electricity_price_eur_kwh
    series=country_name
    yAxisTitle="€ / kWh (household, all taxes)"
    xAxisTitle="Half-year"
    yFmt="0.00"
/>

Eurostat publishes household prices **twice a year**, and the price charts in the explorer use
an annual average of the two halves. That average is not a neutral summary, which
is why the warehouse keeps the published half-years beside it. The difference is
the 2021–23 energy crisis: the mean absolute half-over-half change was **19%**
across countries in 2022 and 13% in 2023, against 3–5% through the 2010s.

The spikes above are single half-years. Averaged into an annual figure they
become a smooth rise, which reads as a gradual squeeze rather than the step
change households actually saw.

```sql biggest_half_moves
select
    country_name,
    period,
    electricity_price_eur_kwh,
    electricity_price_eur_kwh - change_vs_previous_half_eur_kwh as previous_price,
    change_vs_previous_half_pct,
    avg(electricity_price_eur_kwh) over (partition by country_iso3, year) as annual_average
from warehouse.eu_electricity_prices_semiannual
where change_vs_previous_half_eur_kwh is not null
order by abs(change_vs_previous_half_pct) desc
limit 8
```

<DataTable data={biggest_half_moves} rows=8>
    <Column id=country_name title="Country"/>
    <Column id=period title="Half-year" align=left/>
    <Column id=previous_price title="Previous half" fmt="0.000"/>
    <Column id=electricity_price_eur_kwh title="This half" fmt="0.000"/>
    <!-- Two clauses: a bare +0"%" renders -77% as "-+77%". -->
    <Column id=change_vs_previous_half_pct title="Change" fmt='+0"%";-0"%"'/>
    <Column id=annual_average title="Year's average" fmt="0.000"/>
</DataTable>

The Netherlands is the clearest case, and the one that should make you distrust
any annual number here: €0.034/kWh in 2022-S1 against €0.142 in S2, as that
year's energy-tax cuts landed in the first half. The annual average of €0.088 is
a price no Dutch household paid in either half. The low figure is real and
published, not a loading error, which is why the tests on this column allow it.

## Averaged across countries

**The EU has two average prices. Eurostat's weights each member by what its
households consume. The plain mean of the national prices counts Malta as much as
Germany, and runs well below it.**

```sql eu_averages
-- Eurostat's own EU average beside the plain mean of the same members
-- (eu_average_price.sql).
select period_start_date, 'Weighted by consumption (Eurostat)' as average, eu27_eur_kwh as eur_kwh
from warehouse.eu_average_price
union all
select period_start_date, 'Plain mean of the members', member_mean_eur_kwh
from warehouse.eu_average_price
order by 1
```

```sql eu_gap
select
    max(n_members)                                                           as n_members,
    arg_max(period, period_start_date)                                       as latest_period,
    arg_max(eu27_eur_kwh, period_start_date)                                 as latest_weighted,
    arg_max(member_mean_eur_kwh, period_start_date)                          as latest_plain,
    arg_max(100.0 * (1 - member_mean_eur_kwh / eu27_eur_kwh), period_start_date) as latest_gap_pct,
    max(100.0 * (1 - member_mean_eur_kwh / eu27_eur_kwh))                    as widest_gap_pct,
    min(100.0 * (1 - member_mean_eur_kwh / eu27_eur_kwh))                    as narrowest_gap_pct,
    arg_min(period, 1 - member_mean_eur_kwh / eu27_eur_kwh)                  as narrowest_period,
    100.0 * (max(eu27_eur_kwh) filter (where period = '2022-S2')
        / max(eu27_eur_kwh) filter (where period = '2021-S1') - 1)           as weighted_rise_pct,
    100.0 * (max(member_mean_eur_kwh) filter (where period = '2022-S2')
        / max(member_mean_eur_kwh) filter (where period = '2021-S1') - 1)    as plain_rise_pct
from warehouse.eu_average_price
```

```sql crisis_by_member
-- Why the two averages parted in the crisis: the rise in each member's price,
-- for the two most populous members and for the ones where it at least doubled.
with rise as (
    select
        country_iso3,
        100.0 * (max(electricity_price_eur_kwh) filter (where period = '2022-S2')
            / max(electricity_price_eur_kwh) filter (where period = '2021-S1') - 1) as rise_pct
    from warehouse.eu_electricity_prices_semiannual
    where is_eu_member
      and period in ('2021-S1', '2022-S2')
    group by country_iso3
)

select
    max(rise_pct) filter (where country_iso3 = 'DEU') as germany_rise_pct,
    max(rise_pct) filter (where country_iso3 = 'FRA') as france_rise_pct,
    count(*) filter (where rise_pct >= 100)           as n_doubled
from rise
```

<LineChart
    data={eu_averages}
    x=period_start_date
    y=eur_kwh
    series=average
    seriesColors={{
        'Weighted by consumption (Eurostat)': ['#2a78d6', '#3987e5'],
        'Plain mean of the members': ['#eb6834', '#d95926']
    }}
    yFmt="0.00"
    xFmt="yyyy"
    title="Two averages of the EU household price"
    subtitle="€ per kWh, all taxes, by half-year"
/>

In <Value data={eu_gap} column=latest_period/> Eurostat's average was €<Value data={eu_gap} column=latest_weighted fmt="0.000"/> per kWh and the plain mean of the <Value data={eu_gap} column=n_members/> members €<Value data={eu_gap} column=latest_plain fmt="0.000"/> per kWh, which is <Value data={eu_gap} column=latest_gap_pct fmt='0.0"%"'/> lower. The gap has been as wide as <Value data={eu_gap} column=widest_gap_pct fmt='0.0"%"'/> and was narrowest in <Value data={eu_gap} column=narrowest_period/> at <Value data={eu_gap} column=narrowest_gap_pct fmt='0.0"%"'/> of Eurostat's figure.

It closed in the crisis because the crisis landed unevenly. From 2021-S1 to 2022-S2 the plain mean rose <Value data={eu_gap} column=plain_rise_pct fmt='0"%"'/> and Eurostat's average <Value data={eu_gap} column=weighted_rise_pct fmt='0"%"'/> over the same eighteen months: prices rose <Value data={crisis_by_member} column=germany_rise_pct fmt='0"%"'/> in Germany and <Value data={crisis_by_member} column=france_rise_pct fmt='0"%"'/> in France, the two most populous members, and at least doubled in <Value data={crisis_by_member} column=n_doubled/> smaller ones.

The weighted figure is what the average EU household pays, and the plain one is
what the average member country charges. Neither is wrong, but only the first is
the EU's average, and it is the one every chart on this site labelled as an EU
average shows. Eurostat also prices 14 countries outside the EU, most of them
cheaper than either line, so a mean over everything it publishes is lower again
and is the average of no group anyone would name.

<small>Eurostat computes its EU figure by weighting each national price with that country's latest household consumption; see the <a href="https://ec.europa.eu/eurostat/cache/metadata/en/nrg_pc_204_sims.htm">reference metadata</a>, section 18.</small>
