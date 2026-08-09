---
title: Currency
description: The ECB's daily euro reference rates, what the 30% of days with no rate cost you, and why the same electricity price rose 35% or 13.5% depending on which currency you counted in.
---

Every other source in this warehouse publishes once a year. The European Central
Bank publishes euro reference rates every business day, so this is the first
table here with a grain finer than a year. Three problems come with it that an
annual warehouse never has to answer: the calendar has holes in it, a rate has a
direction, and converting a flow works differently from converting a balance.

```sql coverage
select
    sum(n_rows) as n_rows,
    sum(n_published) as n_published,
    sum(n_carried) as n_carried,
    sum(n_stale) as n_stale,
    100.0 * sum(n_carried) / sum(n_rows) as carried_pct
from warehouse.fx_coverage
```

```sql gap_days
select
    sum(calendar_days) as calendar_days,
    sum(publication_days) as publication_days,
    sum(days_with_no_fixing) as missing_days,
    sum(days_with_no_fixing) filter (where is_weekday) as missing_weekdays
from warehouse.fx_calendar_gaps
```

<Grid cols=4>
    <BigValue data={gap_days} value=publication_days fmt='#,##0' title="Publication days since 1999"/>
    <BigValue data={gap_days} value=missing_days fmt='#,##0' title="Calendar days with no rate"/>
    <BigValue data={coverage} value=carried_pct fmt='0.0"%"' title="Rows carried forward"/>
    <BigValue data={coverage} value=n_stale fmt='#,##0' title="Rows too stale to use"/>
</Grid>

## The 30% of days that have no rate

The ECB fixes rates on TARGET settlement days, so most of the calendar is empty.

Of <Value data={gap_days} column=calendar_days fmt='#,##0'/> calendar days since the series began, <Value data={gap_days} column=publication_days fmt='#,##0'/> carry a fixing. The rest are weekends, and <Value data={gap_days} column=missing_weekdays/> weekdays that are not.

```sql by_weekday
select
    day_name,
    day_of_week,
    calendar_days,
    publication_days,
    days_with_no_fixing,
    100.0 * publication_days / calendar_days as published_pct
from warehouse.fx_calendar_gaps
order by day_of_week
```

<BarChart
    data={by_weekday}
    x=day_name
    y=published_pct
    title="Share of days with a published fixing, by weekday"
    yFmt='0"%"'
    yMax={100}
    sort={false}
/>

Saturday and Sunday are structurally zero. The weekdays fall short of 100% by the
TARGET closures: Good Friday, Easter Monday, 1 May, 25 and 26 December, and
1999-12-31 for the millennium changeover. That is why this project carries no
holiday calendar. No weekday rule predicts those dates, so they are observed as
absences in the data rather than asserted from a list somebody would have to
maintain forever.

<Alert status=info>

**So what.** A transaction dated on a Sunday still has to be converted, and every
option here is a modelling decision rather than a lookup. Interpolating between
Friday and Monday invents a rate nobody could have dealt at, and it needs the
future to compute the past. Leaving the rate null pushes the same decision into
every downstream query, to be answered differently each time. So
`marts.fct_fx_rates_daily` carries the last fixing forward, which is what a
finance system does, and records `rate_source_date` on every row so you can see
which fixing you are quoting.

</Alert>

## Two ways carrying forward goes wrong

```sql lifecycle
select
    currency_code,
    currency_name,
    first_published_date,
    last_published_date,
    n_published_days,
    longest_gap_days,
    retired_reason,
    replaced_by_currency,
    retirement_is_explained
from warehouse.fx_currencies
where is_quoted and (not is_currently_published or has_interior_gap)
order by last_published_date
```

```sql panel
select
    count(*) filter (where is_quoted) as n_quoted,
    count(*) filter (where is_quoted and is_currently_published) as n_live,
    count(*) filter (where is_quoted and not is_currently_published) as n_stopped,
    count(*) filter (where retired_reason = 'euro_adoption') as n_euro,
    count(*) filter (where retired_reason = 'redenomination') as n_redenominated,
    count(*) filter (
        where is_quoted and not is_currently_published and not retirement_is_explained
    ) as n_unexplained
from warehouse.fx_currencies
```

**One: outside a currency's lifetime there is nothing to carry.** The ECB's panel
changes over time.

Of the <Value data={panel} column=n_quoted/> codes the series has ever quoted, <Value data={panel} column=n_live/> are still live and <Value data={panel} column=n_stopped/> stopped.

Of those, <Value data={panel} column=n_euro/> stopped on the last business day before their country adopted the euro: the Greek drachma in 2000, the Croatian kuna in 2022, the Bulgarian lev at the end of 2025. <Value data={panel} column=n_redenominated/> stopped at a redenomination, where the same money continued under a new code, as with the Turkish lira at 1,000,000:1 and the Romanian leu at 10,000:1, both in 2005. The remaining <Value data={panel} column=n_unexplained/> simply ceased, and this project does not guess at why.

So the dense series is built per currency between its first and last fixing, and
a euro-era drachma never gets invented.

<DataTable data={lifecycle} rows=20>
    <Column id=currency_code title="Code"/>
    <Column id=currency_name title="Currency"/>
    <Column id=last_published_date title="Last quoted"/>
    <Column id=n_published_days title="Fixings" fmt='#,##0'/>
    <Column id=longest_gap_days title="Longest gap, days" fmt='#,##0'/>
    <Column id=retired_reason title="Reason"/>
    <Column id=replaced_by_currency title="Became"/>
</DataTable>

**Two: a suspended quote is not a long weekend.** The longest closure in the whole
series is five days, so the carry-forward is capped at seven. That fills every
weekend and holiday while refusing exactly two gaps, both of which are currency
crises rather than calendars. The Icelandic króna has no reference rate for 3,341
days between the 2008 banking collapse and February 2018, and the Argentine peso
none for 34 days after the January 2002 breaking of the dollar peg.

Those <Value data={coverage} column=n_stale fmt='#,##0'/> rows exist with a null rate and `is_rate_stale` set, giving an absence you can count instead of nine years of a rate that had stopped being real.

## Spot or average

Converting a stock (a balance, a position at an instant) uses the closing rate.
Converting a flow (revenue, spend, a price paid across a period) uses the period
average. Getting the two the wrong way round is invisible in the output, because
a plausible number comes out either way.

```sql spot_vs_avg
select
    -- A real date on the axis, not the label: 27 category ticks render as "2..."
    -- and sort as strings.
    period_start_date,
    period_label,
    avg_units_per_eur,
    period_end_units_per_eur,
    period_end_vs_avg_pct,
    intra_period_range_pct
from warehouse.fx_periods
where period_type = 'year' and quote_currency = '${inputs.ccy.value}' and period_is_complete
order by period_start_date
```

```sql ccy_list
select
    quote_currency as value,
    quote_currency as label
from warehouse.fx_periods
where period_type = 'year'
group by quote_currency
having count(*) >= 20
order by label
```

<Dropdown data={ccy_list} name=ccy value=value label=label defaultValue="USD" title="Currency"/>

```sql worst
select
    period_label,
    period_end_vs_avg_pct
from warehouse.fx_periods
where period_type = 'year' and quote_currency = '${inputs.ccy.value}' and period_is_complete
order by abs(period_end_vs_avg_pct) desc
limit 1
```

For {inputs.ccy.label}, the year where the two answers diverge most is <Value data={worst} column=period_label/>, at <Value data={worst} column=period_end_vs_avg_pct fmt='0.0"%"'/>. A full year of flows converted at the closing rate instead of the average is misstated by that much, which is often more than the margin of the business doing the converting.

<LineChart
    data={spot_vs_avg}
    x=period_start_date
    y={["avg_units_per_eur", "period_end_units_per_eur"]}
    title="Annual average against year-end rate, per EUR"
    yFmt='0.000'
    xFmt='yyyy'
/>

<Alert status=warning>

**The averages are taken over published fixings, not over calendar days.**
Averaging the gap-filled daily table would count every Friday three times, since
Friday, Saturday and Sunday all carry Friday's rate, and four or five times
around a holiday weekend. That weights the mean toward whichever weekday sits
next to a closure. There is a second trap in the same model: `avg_eur_per_unit`
is not 1 / `avg_units_per_eur`, because the mean of reciprocals is not the
reciprocal of the mean. For EUR/USD the two disagree by 0.07% in a calm year and
0.53% in 2008.

</Alert>

## What this changes about a number already on the site

The warehouse holds exactly one euro-denominated measurement, Eurostat's
household electricity prices, sitting beside GDP in dollars. Until there was an
FX table the two could not be compared at all. They can now, and the comparison
turns out to matter.

```sql eur_vs_usd
with paired as (
    select country_iso3
    from warehouse.eu_electricity_prices_semiannual
    where period in ('2021-S1', '2022-S2')
    group by country_iso3
    having count(*) = 2
)
select
    period_start_date,
    avg(electricity_price_eur_kwh) as eur_kwh,
    avg(electricity_price_usd_kwh) as usd_kwh,
    min(usd_per_eur_period_avg) as usd_per_eur
from warehouse.eu_electricity_prices_semiannual
where country_iso3 in (select country_iso3 from paired)
group by period_start_date
order by period_start_date
```

```sql crisis
with paired as (
    select country_iso3
    from warehouse.eu_electricity_prices_semiannual
    where period in ('2021-S1', '2022-S2')
    group by country_iso3
    having count(*) = 2
),
ends as (
    select
        period,
        avg(electricity_price_eur_kwh) as eur_kwh,
        avg(electricity_price_usd_kwh) as usd_kwh,
        min(usd_per_eur_period_avg) as usd_per_eur
    from warehouse.eu_electricity_prices_semiannual
    where country_iso3 in (select country_iso3 from paired) and period in ('2021-S1', '2022-S2')
    group by period
)
select
    (select count(*) from paired) as n_countries,
    100.0 * (max(eur_kwh) filter (where period = '2022-S2')
        / max(eur_kwh) filter (where period = '2021-S1') - 1) as eur_rise_pct,
    100.0 * (max(usd_kwh) filter (where period = '2022-S2')
        / max(usd_kwh) filter (where period = '2021-S1') - 1) as usd_rise_pct,
    max(usd_per_eur) filter (where period = '2021-S1') as fx_before,
    max(usd_per_eur) filter (where period = '2022-S2') as fx_after
from ends
```

<Grid cols=3>
    <BigValue data={crisis} value=eur_rise_pct fmt='0.0"%"' title="Price rise, 2021-S1 to 2022-S2, in EUR"/>
    <BigValue data={crisis} value=usd_rise_pct fmt='0.0"%"' title="... the same rise, in USD"/>
    <BigValue data={crisis} value=n_countries title="Countries, present in both halves"/>
</Grid>

Across the <Value data={crisis} column=n_countries/> countries Eurostat covers in both halves, the average household electricity price rose <Value data={crisis} column=eur_rise_pct fmt='0.0"%"'/> in euros and <Value data={crisis} column=usd_rise_pct fmt='0.0"%"'/> in dollars over the same eighteen months. The euro fell from <Value data={crisis} column=fx_before fmt='0.000'/> to <Value data={crisis} column=fx_after fmt='0.000'/> against the dollar while that was happening.

<LineChart
    data={eur_vs_usd}
    x=period_start_date
    y={["eur_kwh", "usd_kwh"]}
    title="EU average household electricity price, per kWh"
    yFmt='0.000'
    xFmt='yyyy-mmm'
/>

<Alert status=info>

**So what.** Both numbers are right. A euro-area household did face a 35% rise,
and a dollar-denominated buyer of the same electricity did face 13.5%. A chart
titled "European electricity prices" with no stated currency is reporting the
exchange rate alongside the energy market. This warehouse already carried that
warning in prose, from the case where Japan cut emissions 21% between 2010 and
2024 and still scored 10% worse on carbon intensity because the yen fell 28%
against the dollar. It is now a column rather than a paragraph.

</Alert>

## Limitations

- **The reference rate is not a dealable rate.** The ECB publishes it at 16:00
  CET for information, and nobody transacts at it. Use it for reporting and
  translation, not for pricing a trade.
- **The fiscal calendar in `dim_date` is a policy rather than a fact.** It comes
  from a project variable, set to April for the UK and Japanese convention, and
  the value used is carried on every row. The same Tuesday belongs to a different
  fiscal year under a US federal October or a continental January start.
- **`dim_date` is a calendar, not a market calendar.** It knows weekends. It does
  not know trading days, settlement days or public holidays in any jurisdiction,
  and where this warehouse needs those it reads them out of the observed
  fixings.

The tables are `marts.dim_date`, `marts.dim_currency`,
`marts.fct_fx_rates_published` (the fixings as published, and the only
incremental model in the project), `marts.fct_fx_rates_daily` (gap-filled) and
`marts.fct_fx_rates_periods` (month, quarter, half and year). All five ship in
the [data release](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/releases/latest).
