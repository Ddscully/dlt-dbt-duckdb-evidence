---
title: Retention
description: Retention by acquisition cohort, and why a seasonal business reads its autumn as loyalty.
sidebar_position: 2
---

[← Retail Transactions](/retail)

**Retention falls for the first months after a customer's first order and then rises again at month twelve. That rise is the autumn trading season, not loyalty.**

A transaction log answers a question no country-year can: do the customers won
in March still buy in September? The usual tool is a retention triangle, which
groups customers by the month of their first purchase and counts how many are
still active in each month afterwards.

```sql triangle
select
    cohort_month,
    months_since_first_order,
    retention_pct
from warehouse.retail_cohorts
where months_since_first_order between 1 and 12
  and not is_left_censored_cohort
  and is_complete_period
```

<Heatmap
    data={triangle}
    x=months_since_first_order
    y=cohort_month
    value=retention_pct
    valueFmt='0.0"%"'
    xSort=months_since_first_order
    ySort=cohort_month
    ySortOrder=desc
    valueLabels=false
    title="Retention by acquisition cohort"
    subtitle="Month 0 omitted: it is 100% by construction and would flatten the scale"
/>

```sql curve
-- Month 0 is excluded deliberately. It is 100% for every cohort by definition,
-- and leaving it in rescales the axis until the 21%-to-14% decay the chart
-- exists to show becomes a flat line along the bottom.
select
    months_since_first_order,
    avg(retention_pct)    as retention_pct,
    sum(active_customers) as active_customers,
    count(*)              as n_cohorts
from warehouse.retail_cohorts
where not is_left_censored_cohort
  and is_complete_period
  and months_since_first_order between 1 and 12
group by months_since_first_order
order by months_since_first_order
```

<LineChart data={curve} x=months_since_first_order y=retention_pct title="Average retention curve" subtitle="Months 1–12, cohorts that could be observed that far" yFmt='0.0"%"' xAxisTitle="Months since first order" yMin=0/>

The curve decays as expected, and then stops:

```sql bounce
-- The low is found, not assumed: which month it lands on moves with the data.
select
    max(retention_pct) filter (where months_since_first_order = 1)  as m1,
    min(retention_pct)                                              as low,
    arg_min(months_since_first_order, retention_pct)                as low_month,
    max(retention_pct) filter (where months_since_first_order = 12) as m12
from (
    select months_since_first_order, avg(retention_pct) as retention_pct
    from warehouse.retail_cohorts
    where not is_left_censored_cohort
      and is_complete_period
      and months_since_first_order between 1 and 12
    group by months_since_first_order
)
```

Retention falls from <Value data={bounce} column=m1 fmt='0.0"%"'/> in month 1 to a low of <Value data={bounce} column=low fmt='0.0"%"'/> at month <Value data={bounce} column=low_month fmt="0"/> and then rises to <Value data={bounce} column=m12 fmt='0.0"%"'/> at month 12. Customers are not becoming more loyal at the one-year mark.

The heatmap explains it, in a direction the line chart averages away. Reading
down a column shows ageing, or what happens to a relationship as it gets older.
Reading along a diagonal shows calendar time, because every cohort passes
through November 2010 on the same day at a different age. The dark band in the
heatmap runs diagonally, and it lands on autumn.

```sql seasonality
select
    case when cast(substr(activity_month, 6, 2) as integer) in (9, 10, 11)
        then 'September–November' else 'Rest of year' end  as season,
    avg(retention_pct)                                     as retention_pct,
    count(*)                                               as n_cells
from warehouse.retail_cohorts
where months_since_first_order between 1 and 12
  and not is_left_censored_cohort
  and is_complete_period
group by season
order by retention_pct desc
```

```sql by_calendar_month
-- The month label is built by substring rather than by casting a number.
-- Evidence's extractor writes every numeric column to parquet as DOUBLE, so
-- `cast(month as varchar)` on a page renders "1.0". The month is already text
-- inside 'YYYY-MM', so taking it out as text avoids the problem, and `strftime`
-- supplies the readable name. Ordering is on the integer, with sort=false on the
-- chart so query order survives.
select
    strftime(cast(activity_month || '-01' as date), '%b')  as calendar_month,
    cast(substr(activity_month, 6, 2) as integer)          as month_number,
    avg(retention_pct)                                     as retention_pct,
    count(*)                                               as n_cells
from warehouse.retail_cohorts
where months_since_first_order between 1 and 12
  and not is_left_censored_cohort
  and is_complete_period
group by calendar_month, month_number
having count(*) >= 6
order by month_number
```

<BarChart data={by_calendar_month} x=calendar_month y=retention_pct title="Average retention by calendar month of activity" subtitle="Every cohort age pooled, which is the diagonal read flat" yFmt='0.0"%"' xAxisTitle=" " sort=false/>

Pooled across every cohort age, a customer is active in <Value data={seasonality} column=retention_pct fmt='0.0"%"'/> of September-to-November months against <Value data={seasonality} column=retention_pct row=1 fmt='0.0"%"'/> for the rest of the year. The business sells Christmas stock to shops, so its customers come back in autumn whenever they were first won. The month-12 rise is the same effect read along the other axis, since a cohort's twelfth month falls in the calendar month it started in.

Few summaries keep those two readings apart. A retention curve averages the
diagonal into the column and presents the result as ageing, which is how a
seasonal business talks itself into a loyalty problem every January.

<Alert status=warning>

**Two caveats, both handled in the table itself and not noted underneath it.**
The triangle is ragged: a cohort formed in November 2011 has no month-12 row
because the extract ends in December, and those cells are missing, not zero, so
rows exist only for months that could have been observed. The first cohort is
also left-censored, since December 2009 is the extract's opening month and its
"new" customers include anyone who had been buying for years already, and every
chart on this page leaves that cohort out.

</Alert>
