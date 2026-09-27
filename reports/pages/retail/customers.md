---
title: Customers
description: How concentrated the revenue is, and the RFM segments that turn it into groups to act on.
sidebar_position: 3
---

[← Retail Transactions](/retail)

**Fifty-eight customers account for about a third of everything sold, and the bottom half of the base for the last 7%.**

## How concentrated the revenue is

Before asking who the customers are, it helps to know how few of them carry the
business.

```sql concentration_curve
-- Customers ranked by spend, then cumulative share of revenue at each percentile
-- of the base. `ceil` rather than `round` so bucket X means "the top X%" exactly;
-- rounding puts the first 29 customers in a bucket labelled 0.
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

<LineChart
    data={concentration_curve}
    x=pct_of_customers
    y=pct_of_revenue
    yMin=0
    yMax=100
    echartsOptions={{xAxis: {min: 0, max: 100}}}
    color="#2a78d6"
    xAxisTitle="Share of customers, richest first (%)"
    yAxisTitle="Share of revenue (%)"
>
    <ReferenceLine x=0 y=0 x2=100 y2=100 label="If every customer spent the same" lineType=dashed/>
</LineChart>

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
    max(n)                                                  as n_customers,
    100.0 * max(case when rn <= n * 0.01 then cum end) / max(tot) as top_1,
    100.0 * max(case when rn <= n * 0.05 then cum end) / max(tot) as top_5,
    100.0 * max(case when rn <= n * 0.20 then cum end) / max(tot) as top_20
from ranked
```

<Grid cols=3>
    <BigValue data={concentration_stats} value=top_1 fmt='0.0"%"' title="Revenue from the top 1% of customers"/>
    <BigValue data={concentration_stats} value=top_5 fmt='0.0"%"' title="…from the top 5%"/>
    <BigValue data={concentration_stats} value=top_20 fmt='0.0"%"' title="…from the top 20%"/>
</Grid>

The distance between the curve and the dashed line is the concentration. The
classic Pareto shorthand is 80/20; this business is steeper than that, at
roughly 77/20, and far steeper still at the very top.

Fifty-eight customers out of <Value data={concentration_stats} column=n_customers fmt="#,##0"/> account for just under a third of everything sold, and the bottom half of the base accounts for the last 6.6%.

<Alert status=info>

**So what.** Concentration this steep changes what a retention number is worth.
A campaign that lifts overall repeat rate by two points but misses the top
percentile has moved almost nothing; losing the top nine of those 58 customers
costs more than losing the bottom 2,900 combined. It also sets the reporting grain: an average order
value or a blended churn rate over 5,835 customers is dominated by people who
contribute a rounding error, which is the argument for the segmentation below
rather than a single headline metric.

**Who acts:** whoever owns account management and the retention budget.
**Cost of getting it wrong:** spreading spend evenly across a base where the top
1% is worth more than the bottom half combined.

</Alert>

## Which customers are worth what

[RFM](https://en.wikipedia.org/wiki/RFM_(market_research)) scores every customer
1–5 on how recently they bought, how often, and how much — turning the
concentration above into groups you can act on differently.

```sql segments
select
    segment,
    count(*)                                                      as customers,
    sum(monetary_gbp)                                             as revenue_gbp,
    100.0 * count(*) / sum(count(*)) over ()                      as pct_customers,
    100.0 * sum(monetary_gbp) / sum(sum(monetary_gbp)) over ()    as pct_revenue,
    median(recency_days)                                          as median_recency_days,
    median(frequency)                                             as median_orders
from warehouse.retail_rfm
group by segment
order by revenue_gbp desc
```

<DataTable data={segments} rows=11>
    <Column id=segment title="Segment"/>
    <Column id=customers title="Customers" fmt="#,##0"/>
    <Column id=pct_customers title="% of base" fmt='0.0"%"'/>
    <Column id=revenue_gbp title="Revenue" fmt='"£"#,##0'/>
    <Column id=pct_revenue title="% of revenue" fmt='0.0"%"' contentType=bar/>
    <Column id=median_recency_days title="Median days since" fmt="#,##0"/>
    <Column id=median_orders title="Median orders" fmt="#,##0"/>
</DataTable>

```sql champions
select
    pct_customers,
    pct_revenue
from (
    select
        segment,
        100.0 * count(*) / sum(count(*)) over ()                   as pct_customers,
        100.0 * sum(monetary_gbp) / sum(sum(monetary_gbp)) over () as pct_revenue
    from warehouse.retail_rfm
    group by segment
)
where segment = 'Champions'
```

Champions are <Value data={champions} column=pct_customers fmt='0.0"%"'/> of the identified customer base and <Value data={champions} column=pct_revenue fmt='0.0"%"'/> of its revenue. That gap is most of what the segmentation is for.

<Alert status=info>

**Two customers who behave identically must score the same.** The standard way
to cut a column into quintiles fills five buckets of equal size, so a run of tied
values gets split wherever the boundary happens to land. Frequency is a small
integer with heavy ties: 1,626 customers have placed exactly one order, and that
method puts them in two different quintiles. Counting the four tied values that
straddle a boundary, 3,227 of 5,881 customers could be scored differently from
someone whose behaviour is identical to theirs.

Cutting on the break points instead keeps equal values together. The buckets
then come out uneven, which is a fact about the customer base and not about the
method. A pipeline check counts any value carrying more than one score, because
the tidier method would still produce five neat buckets and a believable segment
mix. (It is why this step is a Polars `qcut` rather than SQL's `ntile`.)

</Alert>
