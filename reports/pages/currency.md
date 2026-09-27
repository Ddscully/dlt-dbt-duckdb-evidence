---
title: Currency
description: The ECB's daily euro reference rates, what the 30% of days with no rate cost you, and why the same electricity price rose about 36% or 14% depending on which currency you counted in.
sidebar_position: 4
---

The European Central Bank's daily euro reference rates, the only source here that
publishes every business day. A rate has a direction, the calendar has holes in
it, and a flow converts differently from a balance; each turns one figure into a
different reported number.

```sql coverage
select
    sum(n_stale) as n_stale,
    100.0 * sum(n_carried) / sum(n_rows) as carried_pct
from warehouse.fx_coverage
```

```sql gap_days
select
    sum(publication_days) as publication_days,
    sum(days_with_no_fixing) as missing_days
from warehouse.fx_calendar_gaps
```

<Grid cols=4>
    <BigValue data={gap_days} value=publication_days fmt='#,##0' title="Publication days since 1999"/>
    <BigValue data={gap_days} value=missing_days fmt='#,##0' title="Calendar days with no rate"/>
    <BigValue data={coverage} value=carried_pct fmt='0.0"%"' title="Rows carried forward"/>
    <BigValue data={coverage} value=n_stale fmt='#,##0' title="Rows too stale to use"/>
</Grid>

## Almost a third of days have no rate

```sql publication_calendar
select date_day, has_fixing
from warehouse.fx_publication_calendar
order by date_day
```

<CalendarHeatmap
    data={publication_calendar}
    date=date_day
    value=has_fixing
    title="Days with a published fixing"
    subtitle="Dark is a fixing. Pale squares inside the weekday block are closures."
    colorPalette={['#eef3fa', '#2a78d6']}
    legend=false
/>

The ECB fixes on settlement days only: never at weekends, and not on New Year,
Good Friday, Easter Monday, 1 May or Christmas. A Sunday transaction still needs a
rate, so the daily table carries the last fixing forward and records which one it
used. **[Gaps, retirements and suspensions →](/currency/gaps)**

## A year's flows at the closing rate misstate them

```sql ccy_list
select
    currency_code as value,
    currency_code as label
from warehouse.fx_periods
where period_type = 'year'
group by currency_code
having count(*) >= 20
order by label
```

<Dropdown data={ccy_list} name=ccy value=value label=label defaultValue="USD" title="Currency"/>

```sql spot_vs_avg
-- A real date on the axis, not the label: 27 category ticks render as "2..."
-- and sort as strings.
select
    period_start_date,
    avg_units_per_eur        as annual_average,
    period_end_units_per_eur as year_end_rate
from warehouse.fx_periods
where period_type = 'year' and currency_code = '${inputs.ccy.value}' and period_is_complete
order by period_start_date
```

```sql worst
select
    period_label,
    period_end_vs_avg_pct
from warehouse.fx_periods
where period_type = 'year' and currency_code = '${inputs.ccy.value}' and period_is_complete
order by abs(period_end_vs_avg_pct) desc
limit 1
```

<LineChart
    data={spot_vs_avg}
    x=period_start_date
    y={["annual_average", "year_end_rate"]}
    seriesColors={{'Annual Average': '#2a78d6', 'Year End Rate': '#eb6834'}}
    title="Annual average against year-end rate, units per euro"
    yFmt='0.000'
    echartsOptions={{yAxis: {scale: true}}}
    xFmt='yyyy'
/>

A balance converts at the closing rate and a flow at the period average. For {inputs.ccy.label} the two differ most in <Value data={worst} column=period_label/> by <Value data={worst} column=period_end_vs_avg_pct fmt='0.0"%"'/> of the average, which is often more than the margin of the business doing the converting. **[Spot or average →](/currency/spot-or-average)**

## The same price, counted in euros and in dollars

```sql eur_vs_usd
-- Both currencies indexed to the first half of 2021, over one fixed set of
-- countries (eu_price_panel.sql), so the gap between the lines is the exchange
-- rate alone.
with base as (select eur_kwh, usd_kwh from warehouse.eu_price_panel where period = '2021-S1')

select a.period_start_date, 'Priced in euros' as currency, 100 * a.eur_kwh / b.eur_kwh as price_index
from warehouse.eu_price_panel a cross join base b
union all
select a.period_start_date, 'Priced in dollars', 100 * a.usd_kwh / b.usd_kwh
from warehouse.eu_price_panel a cross join base b
order by 1
```

```sql crisis_rise
select
    100.0 * (max(eur_kwh) filter (where period = '2022-S2')
        / max(eur_kwh) filter (where period = '2021-S1') - 1) as eur_rise_pct,
    100.0 * (max(usd_kwh) filter (where period = '2022-S2')
        / max(usd_kwh) filter (where period = '2021-S1') - 1) as usd_rise_pct
from warehouse.eu_price_panel
```

<LineChart
    data={eur_vs_usd}
    x=period_start_date
    y=price_index
    series=currency
    seriesColors={{'Priced in euros': ['#2a78d6', '#3987e5'], 'Priced in dollars': ['#eb6834', '#d95926']}}
    yFmt='0'
    xFmt='yyyy'
    title="EU average household electricity price, first half of 2021 = 100"
>
    <ReferenceLine y=100 label=" "/>
</LineChart>

From the first half of 2021 to the second half of 2022 the same electricity rose <Value data={crisis_rise} column=eur_rise_pct fmt='0"%"'/> in euros and <Value data={crisis_rise} column=usd_rise_pct fmt='0"%"'/> in dollars.

The euro fell against the dollar while European electricity got dearer, so a
dollar-based buyer of the same kilowatt-hour saw a much smaller rise. Both numbers
are right; a chart of "European electricity prices" with no stated currency is
also a chart of the exchange rate. **[The crisis in both currencies →](/currency/spot-or-average)**

<Details title="Limitations">

- **The reference rate is not a dealable rate.** The ECB publishes it at 16:00
  CET for information, and nobody transacts at it. Use it for reporting and
  translation, not for pricing a trade.
- **The fiscal year is a policy, not a fact.** It is set to April here, for the
  UK and Japanese convention, and every row carries the value it was built with.
- **The calendar is not a market calendar.** It knows weekends. It does not know
  trading days, settlement days or public holidays in any jurisdiction, and
  where this warehouse needs those it reads them out of the observed fixings.

The tables are `marts.dim_date`, `marts.dim_currency`,
`marts.fct_fx_rates_published` (the fixings as published, and the only
incremental model in the project), `marts.fct_fx_rates_daily` (gap-filled) and
`marts.fct_fx_rates_periods` (month, quarter, half and year). All five ship in
the [data release](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/releases/latest).

</Details>

<small>Source: <a href="https://frankfurter.dev">ECB reference rates via Frankfurter</a>; electricity prices from <a href="https://ec.europa.eu/eurostat/databrowser/view/nrg_pc_204">Eurostat</a>.</small>
