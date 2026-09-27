---
title: Half-year prices
description: Eurostat's household electricity prices by half-year, and what averaging them into an annual figure hides.
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
across countries in 2022 and 13% in 2023, against 3–4% through the 2010s.

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
