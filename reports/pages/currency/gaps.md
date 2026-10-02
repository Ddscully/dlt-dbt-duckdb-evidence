---
title: Gaps in the calendar
description: Which days the ECB publishes no rate, how the gaps are filled, and the two cases where filling them would be wrong.
sidebar_position: 1
---

[← Currency](/currency)

**The ECB fixes rates on TARGET settlement days only, so most of the calendar is empty. Filling the gaps is a modelling decision, and there are two places where it must refuse.**

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

## The 30% of days that have no rate

Of <Value data={gap_days} column=calendar_days fmt='#,##0'/> calendar days since the series began, <Value data={gap_days} column=publication_days fmt='#,##0'/> carry a fixing. The rest are weekends, and <Value data={gap_days} column=missing_weekdays/> weekdays that are not.

```sql publication_calendar
select date_day, has_fixing
from warehouse.fx_publication_calendar
order by date_day
```

<CalendarHeatmap
    data={publication_calendar}
    date=date_day
    value=has_fixing
    title="Days with a published fixing, 2023-2025"
    subtitle="Dark is a fixing. Sunday and Saturday are the outer rows; pale squares between them are closures."
    colorPalette={['#eef3fa', '#2a78d6']}
    legend=false
/>

Two of the seven rows in each year are structurally empty — the ECB does not fix
at a weekend. The interesting part is the handful of pale squares punched out of
the weekday block: 1 January, Good Friday, Easter Monday, 1 May, and 25 and 26
December. Seventeen days over the three years.

Read the Easter pair across the years and it moves: 7 and 10 April in 2023, 29
March and 1 April in 2024, 18 and 21 April in 2025. That is why this project
carries no holiday calendar. No weekday rule predicts those dates, the only rule
that does is the Gregorian computus, so they are observed as absences in the data
instead of being asserted from a list somebody would have to maintain forever.
(The series has one closure no rule of any kind would give you: 1999-12-31, taken
for the millennium changeover.)

<Alert status=info>

**So what.** A transaction dated on a Sunday still has to be converted, and every
option here is a modelling decision, not a lookup. Interpolating between Friday
and Monday invents a rate nobody could have dealt at, and it needs the future to
compute the past. Leaving the rate null pushes the same decision into every
downstream query, to be answered differently each time. So the daily table
carries the last fixing forward, which is what a finance system does, and records
the date of the fixing it used on every row, so you can always see which rate you
are quoting.

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
    count(*) filter (
        where is_quoted and not is_currently_published and not retirement_is_explained
    ) as n_unexplained
from warehouse.fx_currencies
```

**One: outside a currency's lifetime there is nothing to carry.** The ECB's panel
changes over time.

Of the <Value data={panel} column=n_quoted/> codes the series has ever quoted, <Value data={panel} column=n_live/> are still live and <Value data={panel} column=n_stopped/> stopped.

Of those, <Value data={panel} column=n_euro/> stopped on the last business day before their country adopted the euro: the Greek drachma in 2000, the Croatian kuna in 2022, the Bulgarian lev at the end of 2025. None stopped at a redenomination: the ECB quotes the Turkish lira and the Romanian leu back to 1999 in their post-2005 units, so their 2005 changes of code never reach the series. The remaining <Value data={panel} column=n_unexplained/> simply ceased, and this project does not guess at why.

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
crises rather than calendars. The Icelandic króna has no reference rate for 3,347
days between the 2008 banking collapse and February 2018, and the Argentine peso
none for 34 days after the January 2002 breaking of the dollar peg.

Those <Value data={coverage} column=n_stale fmt='#,##0'/> rows exist with a null rate and a flag saying so, giving an absence you can count instead of nine years of a rate that had stopped being real.
