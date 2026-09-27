---
title: First order
description: How much of a customer's lifetime value is visible in the size of their first order.
sidebar_position: 4
---

[← Retail Transactions](/retail)

**The size of a customer's first order is the one thing known on the day they arrive, and it carries most of the answer: the top fifth by first order went on to be worth ten times the bottom fifth.**

```sql day_one
-- The left-censored cohort is excluded throughout this section. The extract
-- opens on 2009-12-01, so the "first order" it records for that month's
-- customers is very often not their first order at all — including them would
-- pair a mid-relationship purchase with a lifetime value, which is the one
-- thing this chart must not do.
--
-- Both axes are logged, so both sides have to be positive — and the threshold is
-- a penny rather than zero on purpose. Two customers bought and returned
-- everything, and the signed line amounts do not cancel to exactly zero in
-- floating point: they leave 3.6e-15 and 2.8e-14 behind. Both pass `> 0`, and a
-- log axis obliged by spanning fifteen orders of magnitude to fit them, which
-- flattened all 4,868 real points into a single band.
--
-- 4,868 of the 4,926 non-censored customers survive. The rest are the 47 whose
-- first invoice carried no product line, plus those two and a few genuine
-- all-returns cases.
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
    title="Lifetime value against first order"
    subtitle="One point per customer. Both axes are £, both log scales."
    xAxisTitle="First order"
    seriesColors={{'Ordered once': '#c0932e', 'Ordered again': '#2a78d6'}}
/>

<!--
`progressive: 0` is load-bearing, and nothing about the chart says so. ECharts
switches a scatter series to progressive (chunked, one slice per animation
frame) at `progressiveThreshold: 3000` points, and Evidence exposes neither
setting. This series is 4,868 points. Measured on the built site: 2,900 points
render in 2 s, 4,871 points never finish, and the same 4,871 with progressive
disabled render in 1 s. It is a mode change at a threshold, not a volume
problem — so trimming the data would have "fixed" it while hiding the cause,
and any future scatter here over 3,000 points needs the same two lines.

Both axes are logged through `echartsOptions` rather than through the
component's own support, and each half has its own reason. There is no `xLog`
prop at all — `ScatterPlot` has `yLog` and `yLogBase`, and `xType` only takes
category / value / time. And `yLog` does not survive the round trip from
markdown: `_Chart.svelte` initialises `yType = yLog === true ? 'log' : 'value'`
once, and `yLog=true` in a page arrives as the *string* `"true"`, which is not
`=== true`, so the axis stays linear. `yLog={true}` does set it — and then
picks a 1-to-10 range with the data an order of magnitude above it. Setting
`yAxis.type` directly is the one spelling that works. On a log y axis Evidence
then drops `yAxisTitle` — it is drawn as part of the top axis label, and that
label goes away — so the axis is named in the subtitle instead.
-->


The hard edge running diagonally across the chart is not an artefact. A
customer who ordered once is worth their first order **less whatever they sent
back**, so 1,326 of the 1,504 sit exactly on the line *y = x*, the other 178 fall
below it, and none can rise above it. The gold band is that line; the blue cloud
above it is everyone who came back, and returns pull only 21 of them below it.

### Why the correlation coefficient is the wrong number here

The temptation is to quote a Pearson *r*, which is **0.641** and looks
convincing. It is almost entirely one customer: the largest first order in the
file is £33,168, from an account that went on to spend £235,833, and deleting
that single row takes *r* down to **0.398**. Below £5,000 of first order it is
0.344. A statistic that moves by a quarter of its range when you remove one of
4,868 points is measuring the outlier, not the relationship.

The **rank** correlation does not move at all: 0.592 with the outlier, 0.592
without it, 0.590 below £5,000. Ranks are indifferent to how far out the far end
goes. That is the number to quote, and the quintile table is what it means in
money.

```sql day_one_quintiles
-- `ntile` is the right tool here, unlike in the RFM scoring on the customers page, and the
-- difference is worth stating: this is a five-way split for *presentation*, over
-- a near-continuous currency column with almost no ties. RFM assigns a score
-- that a customer is then treated on, over a column where 1,626 customers share
-- a single value — there, equal-sized buckets cut through the ties and score
-- identical behaviour differently.
with base as (
    select *
    from warehouse.retail_customers
    where not is_left_censored_cohort
      and first_order_gbp >= 0.01
      and net_revenue_gbp >= 0.01
),

scored as (
    select *, ntile(5) over (order by first_order_gbp) as quintile
    from base
)

select
    quintile,
    count(*)                                                     as customers,
    min(first_order_gbp)                                         as band_low,
    max(first_order_gbp)                                         as band_high,
    median(net_revenue_gbp)                                      as median_ltv,
    100.0 * avg(case when is_repeat_customer then 1 else 0 end)  as repeat_pct,
    100.0 * sum(net_revenue_gbp) / sum(sum(net_revenue_gbp)) over () as pct_revenue
from scored
group by quintile
order by quintile
```

<DataTable data={day_one_quintiles} rows=5>
    <Column id=quintile title="Quintile"/>
    <Column id=customers title="Customers" fmt="#,##0"/>
    <Column id=band_low title="First order from" fmt='"£"#,##0'/>
    <Column id=band_high title="…to" fmt='"£"#,##0'/>
    <Column id=median_ltv title="Median lifetime value" fmt='"£"#,##0' contentType=bar/>
    <Column id=repeat_pct title="Ordered again" fmt='0"%"'/>
    <Column id=pct_revenue title="% of cohort revenue" fmt='0.0"%"'/>
</DataTable>

Median lifetime value runs £191 → £410 → £714 → £905 → £1,885 across the
quintiles. The top fifth spent 6.8 times what the bottom fifth did on day one
(median £751 against £110) and went on to be worth **9.9 times** as much, so the
signal amplifies instead of merely persisting, and that fifth accounts for 44.8%
of the cohort's revenue.

One wrinkle: the repeat rate is not monotonic. It climbs 58% → 64% → 73%, dips
to 72% in the fourth quintile, then reaches 78% in the fifth. Whatever separates
a £300 first order from a £400 one, it is not whether the customer comes back.

<Alert status=info>

**So what.** This is the only genuinely forward-looking number in the retail section. The
[concentration curve](/retail/customers) says a small group carries the business; this says that
group is largely identifiable on the day it arrives, from data that already exists
at the point of sale. A first order over £516 puts a customer in the fifth that
generates 45% of revenue, at just under 78% odds of ordering again.

**Who acts:** whoever owns acquisition spend and onboarding. Bidding the same
amount for every new customer, or running the same welcome sequence at all of
them, is leaving the difference between £191 and £1,885 of expected value on the
table.

**Cost of getting it wrong:** the causation runs the other way just as easily. A
big first order may signal a bigger business without creating a better customer.
So this identifies who to look after; it does not say that pushing a first order
from £200 to £500 buys you the difference in lifetime value. The customers here
are shops, and a shop's opening order is mostly a statement about the shop.

</Alert>
