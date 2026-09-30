---
title: Spot or average
description: The closing rate against the period average for any currency, and the two averaging traps in a gap-filled table.
sidebar_position: 2
---

[← Currency](/currency)

**Converting a balance uses the closing rate; converting a flow uses the period average. Getting them the wrong way round is invisible in the output, because a plausible number comes out either way.**

```sql spot_vs_avg
select
    -- A real date on the axis, not the label: 27 category ticks render as "2..."
    -- and sort as strings.
    period_start_date,
    period_label,
    avg_units_per_eur        as annual_average,
    period_end_units_per_eur as year_end_rate,
    period_end_vs_avg_pct,
    intra_period_range_pct
from warehouse.fx_periods
where period_type = 'year' and currency_code = '${inputs.ccy.value}' and period_is_complete
order by period_start_date
```

```sql stale_years
-- Years whose closing rate is a fixing older than the carry `fct_fx_rates_daily`
-- allows. Not a headline for any currency in the dropdown — the worst
-- divergence is always a real crisis with a same-day fixing — but the year-end
-- point is drawn on the chart below like any other, and it is not like any
-- other.
select
    period_label,
    period_end_stale_days,
    last_rate_date,
    period_end_units_per_eur
from warehouse.fx_periods
where period_type = 'year'
  and currency_code = '${inputs.ccy.value}'
  and period_is_complete
  and period_end_is_stale
order by period_start_date
```

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

```sql worst
select
    period_label,
    period_end_vs_avg_pct
from warehouse.fx_periods
where period_type = 'year' and currency_code = '${inputs.ccy.value}' and period_is_complete
order by abs(period_end_vs_avg_pct) desc
limit 1
```

For {inputs.ccy.label}, the two answers diverge most in <Value data={worst} column=period_label/> by <Value data={worst} column=period_end_vs_avg_pct fmt='0.0"%"'/> of the annual average. A full year of flows converted at the closing rate instead of the average is misstated by that much, which is often more than the margin of the business doing the converting.

<LineChart
    data={spot_vs_avg}
    x=period_start_date
    y={["annual_average", "year_end_rate"]}
    title="Annual average against year-end rate, per EUR"
    yFmt='0.000'
    xFmt='yyyy'
/>

<!-- The first paragraph below wraps and carries <Value> components, so its
markdown stops processing at the first line break (see the one-source-line
rule in .agents/skills/building-evidence-reports). Emphasis and code marks
past line one render as literal characters, silently. The flag name is in a
second, component-free paragraph for that reason, where marks do work. -->
{#if stale_years.length > 0}

<Alert status=warning>

**{inputs.ccy.label}'s year-end rate for <Value data={stale_years} column=period_label/> is stale.** The closing
value the chart plots for that year is the fixing of <Value data={stale_years} column=last_rate_date fmt='d mmm yyyy'/> —
<Value data={stale_years} column=period_end_stale_days/> days before the year ended — because the ECB stopped
publishing this currency partway through it. The number is a true statement about converting at the last
available rate, which is why it is shown rather than blanked, but it is not a year-end rate in the sense the
other points on this line are.

The published data carries a `period_end_is_stale` flag on it.

</Alert>

{/if}

<Alert status=warning>

**The averages are taken over published fixings, not over calendar days.**
Averaging the gap-filled daily table would count every Friday three times, since
Friday, Saturday and Sunday all carry Friday's rate, and four or five times
around a holiday weekend. That weights the mean toward whichever weekday sits
next to a closure. There is a second trap in the same table: the average of
euros-per-unit is not one divided by the average of units-per-euro, because the
mean of reciprocals is not the reciprocal of the mean. For EUR/USD the two
disagree by 0.07% in a calm year and 0.53% in 2008.

</Alert>

## Which currency a price is counted in

The warehouse holds one euro-denominated measurement, Eurostat's household
electricity prices, beside GDP in dollars. The FX table is what lets the two be
compared, and the comparison matters.

```sql eur_vs_usd
-- Eurostat's own EU average (eu_average_price.sql), which weights each member
-- by its household consumption. A mean over the countries in the table is a
-- different and lower number.
select
    period_start_date,
    eu27_eur_kwh as price_in_euros,
    eu27_usd_kwh as price_in_dollars
from warehouse.eu_average_price
order by period_start_date
```

```sql crisis
with ends as (
    select period, eu27_eur_kwh, eu27_usd_kwh, usd_per_eur
    from warehouse.eu_average_price
    where period in ('2021-S1', '2022-S2')
)
select
    100.0 * (max(eu27_eur_kwh) filter (where period = '2022-S2')
        / max(eu27_eur_kwh) filter (where period = '2021-S1') - 1) as eur_rise_pct,
    100.0 * (max(eu27_usd_kwh) filter (where period = '2022-S2')
        / max(eu27_usd_kwh) filter (where period = '2021-S1') - 1) as usd_rise_pct,
    100.0 * (max(usd_per_eur) filter (where period = '2022-S2')
        / max(usd_per_eur) filter (where period = '2021-S1') - 1) as fx_change_pct,
    max(usd_per_eur) filter (where period = '2021-S1') as fx_before,
    max(usd_per_eur) filter (where period = '2022-S2') as fx_after
from ends
```

<Grid cols=3>
    <BigValue data={crisis} value=eur_rise_pct fmt='0.0"%"' title="Price rise, 2021-S1 to 2022-S2, in EUR"/>
    <BigValue data={crisis} value=usd_rise_pct fmt='0.0"%"' title="... the same rise, in USD"/>
    <BigValue data={crisis} value=fx_change_pct fmt='0.0"%"' title="The euro against the dollar, same months"/>
</Grid>

The EU's average household electricity price, Eurostat's own figure for the 27 members, rose <Value data={crisis} column=eur_rise_pct fmt='0.0"%"'/> in euros and <Value data={crisis} column=usd_rise_pct fmt='0.0"%"'/> in dollars over the same eighteen months. The euro fell from <Value data={crisis} column=fx_before fmt='0.000'/> to <Value data={crisis} column=fx_after fmt='0.000'/> against the dollar while that was happening.

<LineChart
    data={eur_vs_usd}
    x=period_start_date
    y={["price_in_euros", "price_in_dollars"]}
    title="EU average household electricity price, per kWh"
    yFmt='0.000'
    xFmt='yyyy-mmm'
/>

<Alert status=info>

**So what.** Both numbers are right. A household paying in euros did face a 26% rise,
and a dollar-denominated buyer of the same electricity did face 6%. A chart
titled "European electricity prices" with no stated currency is reporting the
exchange rate alongside the energy market. This warehouse already carried that
warning in prose, from the case where Japan cut emissions 21% between 2010 and
2024 and still scored 10% worse on carbon intensity measured in current dollars:
the yen lost 42% of its dollar value, so Japan's GDP counted in dollars fell 28%.
It is a column now, not a paragraph.

</Alert>
