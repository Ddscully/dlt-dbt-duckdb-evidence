---
title: Retail Transactions
description: One online retailer's order lines, at the finest grain in the warehouse and the only source here recording individual purchases rather than published statistics.
sidebar_position: 3
---

A UK gift wholesaler's complete transaction log: every line of every invoice over
two years, uncleaned by anyone
([UCI's Online Retail II](https://archive.ics.uci.edu/dataset/502/online+retail+ii)).
It is the only source here recording individual purchases rather than published
statistics.

```sql shape
select
    n_lines,
    n_customers,
    revenue_gbp,
    100.0 * revenue_gbp_anonymous / revenue_gbp       as pct_revenue_anonymous
from warehouse.retail_headline
```

<Grid cols=4>
    <BigValue data={shape} value=n_lines title="Order lines" fmt="#,##0"/>
    <BigValue data={shape} value=n_customers title="Customer ids" fmt="#,##0"/>
    <BigValue data={shape} value=revenue_gbp title="Net revenue" fmt='"£"#,##0'/>
    <BigValue data={shape} value=pct_revenue_anonymous title="Revenue with no customer" fmt='0.0"%"'/>
</Grid>

## Trade peaks every autumn

```sql monthly
-- A real date on the axis, not the 'YYYY-MM' label: 25 category ticks render as
-- "2...".
select
    cast(invoice_month || '-01' as date) as month_start,
    sum(revenue_gbp)                     as revenue_gbp
from warehouse.retail_daily
group by invoice_month
order by invoice_month
```

<AreaChart
    data={monthly}
    x=month_start
    y=revenue_gbp
    yFmt="gbp0k"
    fillColor="#1baf7a"
    lineColor="#1baf7a"
    title="Net revenue by month"
/>

The business sells Christmas stock to shops, so each autumn is its year. Getting
to a revenue figure at all means settling what the one amount column holds:
postage, bank fees, bad-debt adjustments and stock write-offs sit in it beside the
goods. **[What counts as revenue →](/retail/revenue)**

## Customers come back in autumn, whenever they were won

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
    valueFmt='0"%"'
    xSort=months_since_first_order
    ySort=cohort_month
    ySortOrder=desc
    valueLabels=false
    nullsZero=false
    cellHeight=16
    colorPalette={['#f3f7fc', '#2a78d6', '#0d2f5c']}
    title="Share of each month's new customers who buy again, by months since their first order"
/>

Read down a column and retention decays with age; read along a diagonal and every
cohort lights up in the same autumn. A retention curve averages the two together,
which is how a seasonal business talks itself into a loyalty problem every
January. **[Retention →](/retail/retention)**

## A fifth of the customers bring three-quarters of the revenue

```sql concentration_curve
-- Customers ranked by spend, then cumulative share of revenue at each percentile
-- of the base. `ceil` rather than `round` so bucket X means "the top X%" exactly.
with ranked as (
    select
        monetary_gbp,
        row_number() over (order by monetary_gbp desc)  as rn,
        count(*) over ()                                as n_customers,
        sum(monetary_gbp) over (
            order by monetary_gbp desc rows unbounded preceding
        )                                               as cumulative_gbp,
        sum(monetary_gbp) over ()                       as total_gbp
    from warehouse.retail_rfm
    where monetary_gbp > 0
),

curve as (
    select
        ceil(100.0 * rn / n_customers)                  as pct_of_customers,
        max(100.0 * cumulative_gbp / total_gbp)         as pct_of_revenue
    from ranked
    group by 1
)

select 0 as pct_of_customers, 0.0 as pct_of_revenue
union all
select * from curve
order by 1
```

```sql concentration_stats
with ranked as (
    select
        row_number() over (order by monetary_gbp desc)  as rn,
        count(*) over ()                                as n,
        sum(monetary_gbp) over (
            order by monetary_gbp desc rows unbounded preceding
        )                                               as cum,
        sum(monetary_gbp) over ()                       as tot
    from warehouse.retail_rfm
    where monetary_gbp > 0
)

select
    100.0 * max(case when rn <= n * 0.01 then cum end) / max(tot) as top_1,
    100.0 * max(case when rn <= n * 0.20 then cum end) / max(tot) as top_20
from ranked
```

<AreaChart
    data={concentration_curve}
    x=pct_of_customers
    y=pct_of_revenue
    yMin=0
    yMax=100
    xFmt='0"%"'
    yFmt='0"%"'
    echartsOptions={{xAxis: {min: 0, max: 100}}}
    fillColor="#2a78d6"
    lineColor="#2a78d6"
    xAxisTitle="Customers, biggest spenders first"
    title="Cumulative share of revenue"
>
    <ReferenceLine x=0 y=0 x2=100 y2=100 label="If every customer spent the same" lineType=dashed/>
</AreaChart>

The top 20% of customers bring in <Value data={concentration_stats} column=top_20 fmt='0"%"'/> of the revenue, and the top 1% alone bring in <Value data={concentration_stats} column=top_1 fmt='0"%"'/> of it. A campaign that lifts the average but misses the top percentile has moved almost nothing. **[Who the customers are →](/retail/customers)**

## The first order predicts most of the rest

```sql day_one
-- The left-censored cohort is excluded: the extract opens on 2009-12-01, so the
-- "first order" it records for that month's customers is often not their first.
-- Both axes are logged, so both sides must be positive, and the threshold is a
-- penny rather than zero: two customers who returned everything leave 3.6e-15
-- and 2.8e-14 behind in floating point, which a log axis obliges by spanning
-- fifteen orders of magnitude.
select
    first_order_gbp,
    net_revenue_gbp,
    case
        when is_repeat_customer then 'Ordered again'
        else 'Ordered once'
    end as customer_type
from warehouse.retail_customers
where not is_left_censored_cohort
  and first_order_gbp >= 0.01
  and net_revenue_gbp >= 0.01
```

<!--
`progressive: 0` is load-bearing. ECharts switches a scatter series to chunked
rendering at 3,000 points, and at 4,868 it never finishes; disabled, it renders in
a second. Both axes are logged through `echartsOptions` because `ScatterPlot` has
no `xLog`, and `yLog=true` arrives from markdown as a string. See
reports/README.md.
-->
<ScatterPlot
    data={day_one}
    x=first_order_gbp
    y=net_revenue_gbp
    series=customer_type
    xFmt='"£"#,##0'
    yFmt='"£"#,##0'
    yMin={1}
    yMax={250000}
    echartsOptions={{xAxis: {type: 'log'}, yAxis: {type: 'log'}}}
    seriesOptions={{progressive: 0, progressiveThreshold: 100000}}
    pointSize=7
    opacity=0.35
    title="Lifetime value against first order, one point per customer"
    subtitle="Both axes £, log scales"
    xAxisTitle="First order"
    seriesColors={{'Ordered once': '#c0932e', 'Ordered again': '#2a78d6'}}
/>

Nobody sits below the diagonal: a customer who ordered once is worth exactly their
first order. Above it, customers whose first order was in the top fifth went on to
be worth about ten times the bottom fifth. **[The first order →](/retail/first-order)**

## Also on this data

- **[Returns and missing customers](/retail/returns)**: 18,286 return lines
  matched to their sales with no key to join on, and the revenue no customer id
  can be attached to.

---

**Sources.** [UCI Machine Learning Repository, Online Retail II](https://archive.ics.uci.edu/dataset/502/online+retail+ii)
(Chen, D., 2019), CC BY 4.0. Exchange rates from the ECB via
[Frankfurter](https://frankfurter.dev). The transaction data is real and
unmodified; the modelling decisions are documented in
`dbt/models/staging/stg_retail_lines.sql` and the retail mart models built on it.
