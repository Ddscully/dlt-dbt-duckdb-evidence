---
title: Retail Transactions
description: One online retailer's order lines, at the finest grain in the warehouse and the only source here recording individual purchases rather than published statistics.
sidebar_position: 3
---

A UK gift wholesaler's complete transaction log: every line of every invoice over
two years, with no cleaning applied by anyone
([UCI's Online Retail II](https://archive.ics.uci.edu/dataset/502/online+retail+ii)).

Three questions have to be settled before a single figure can be reported — what
counts as a return, which rows are revenue, and what to do about orders with
nobody attached to them. Each has an answer that looks reasonable and produces
the wrong number, and each one is worth six figures here.

```sql headline
select * from warehouse.retail_headline
```

```sql shape
select
    n_lines,
    n_invoices,
    n_customers,
    n_products,
    revenue_gbp,
    revenue_eur,
    100.0 * n_lines_anonymous / n_lines               as pct_lines_anonymous,
    100.0 * revenue_gbp_anonymous / revenue_gbp       as pct_revenue_anonymous,
    100.0 * n_lines_fx_carried / n_lines              as pct_fx_carried
from warehouse.retail_headline
```

<Grid cols=4>
    <BigValue data={shape} value=n_lines title="Order lines" fmt="#,##0"/>
    <BigValue data={shape} value=n_invoices title="Invoices" fmt="#,##0"/>
    <BigValue data={shape} value=n_customers title="Customer ids" fmt="#,##0"/>
    <BigValue data={shape} value=revenue_gbp title="Net revenue" fmt='"£"#,##0'/>
</Grid>

Two years and <Value data={shape} column=n_lines fmt="#,##0"/> lines. One row is one product on one invoice, timestamped to the minute.

## What counts as revenue

The source has one amount column and one quantity column. Summing them gives a
number that looks like revenue but isn't, because a third of the row types in
this file are not sales.

```sql line_types
select
    invoice_type,
    item_type,
    n_lines,
    amount_gbp,
    n_write_offs
from warehouse.retail_line_types
order by n_lines desc
```

All seventeen combinations in the extract, rather than the common few. The rare
rows are the ones that cause trouble:

<DataTable data={line_types} rows=17>
    <Column id=invoice_type title="Invoice"/>
    <Column id=item_type title="Item"/>
    <Column id=n_lines title="Lines" fmt="#,##0"/>
    <Column id=amount_gbp title="Amount" fmt='"£"#,##0'/>
</DataTable>

Three of those distinctions change an answer materially:

```sql corrections
select
    sum(amount_gbp) filter (where item_type = 'shipping')      as postage,
    sum(amount_gbp) filter (where item_type = 'fee')           as bank_fees,
    sum(amount_gbp) filter (where invoice_type = 'adjustment') as bad_debt,
    sum(n_write_offs)                                          as write_off_lines,
    sum(amount_gbp) filter (where invoice_type = 'cancellation') as cancellations
from warehouse.retail_line_types
```

- **Postage sits in the same column as the goods.** <Value data={corrections} column=postage fmt='"£"#,##0'/> of it, so any per-unit or per-product figure that includes those rows is partly measuring delivery charges.
- **There is a third invoice prefix.** Six `A` rows worth <Value data={corrections} column=bad_debt fmt='"£"#,##0'/> record bad-debt adjustments, and they hold the only negative prices in the file. Descriptions of this dataset generally mention invoices and cancellations only, which leaves these quietly folded into the totals.
- **Negative quantities are not all returns.** <Value data={corrections} column=write_off_lines fmt="#,##0"/> lines carry a negative quantity on an ordinary sale invoice. All of them are priced at zero and have no customer attached, and the descriptions give them away as inventory write-offs: damage, stock counts, one row labelled `check`. Treating them as returns would raise the return count by about a fifth while leaving the returned value unchanged, so the money would still reconcile and the error would survive review.

<Alert status=info>

**So what.** These are not edge cases to filter out at the end. They decide
whether a revenue figure means anything, so each classification is a `case`
expression in `stg_retail_lines` with a test against it, rather than a caveat in
a README.

</Alert>

## Three currencies, and a shop that trades on Sundays

The fact table carries every amount in three currencies, converted at the
[daily ECB fixing](/currency) for the transaction date. Doing that against a
transaction log turned up something the exchange-rate series had never shown on
its own.

```sql weekday
select
    day_name,
    sum(n_lines)                                     as n_lines,
    sum(revenue_gbp)                                 as revenue_gbp,
    max(fx_rate_is_carried_forward)::int             as fx_carried
from warehouse.retail_daily
group by day_name
order by n_lines desc
```

```sql fx_carried
select
    n_lines_fx_carried,
    100.0 * n_lines_fx_carried / n_lines as pct
from warehouse.retail_headline
```

<BarChart data={weekday} x=day_name y=n_lines swapXY=true title="Order lines by weekday" yFmt="#,##0" sort=false/>

In total <Value data={fx_carried} column=n_lines_fx_carried fmt="#,##0"/> lines, <Value data={fx_carried} column=pct fmt='0.0"%"'/> of the file, convert on a rate the ECB published on an earlier day, and every one of them falls on a Sunday. The business trades on Sundays and barely at all on Saturdays: 139,256 lines against 402. It also closes on the same holidays the euro system does, so no weekday closure ever coincides with an order.

The carry-forward rule and its 7-day cap were written against the FX series
alone, where they filled a gap nothing was querying. With a transaction fact on
top, 13% of the rows depend on them.

```sql monthly_currency
select
    invoice_month,
    sum(revenue_gbp) as revenue_gbp,
    sum(revenue_eur) as revenue_eur
from warehouse.retail_daily
group by invoice_month
order by invoice_month
```

<LineChart data={monthly_currency} x=invoice_month y={['revenue_gbp','revenue_eur']} title="Monthly revenue, GBP and EUR" yFmt="#,##0" seriesColors={{revenue_gbp: '#1baf7a', revenue_eur: '#eda100'}}/>

Both lines track the same trading, so the distance between them is sterling's
exchange rate and nothing the business did. The [electricity price](/currency)
makes the same point at country grain; here it applies to amounts somebody has
to book.

## Who comes back

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
select
    max(retention_pct) filter (where months_since_first_order = 1)  as m1,
    max(retention_pct) filter (where months_since_first_order = 11) as m11,
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

Retention falls from <Value data={bounce} column=m1 fmt='0.0"%"'/> in month 1 to a low of <Value data={bounce} column=m11 fmt='0.0"%"'/> at month 11, then rises to <Value data={bounce} column=m12 fmt='0.0"%"'/> at month 12. Customers are not becoming more loyal at the one-year mark.

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
seasonal business concludes it has a loyalty problem every January.

<Alert status=warning>

**Two caveats, both handled in the table rather than noted underneath it.** The
triangle is ragged: a cohort formed in November 2011 has no month-12 row because
the extract ends in December, and those cells are missing rather than zero, so
rows exist only for months that could have been observed. The first cohort is
also left-censored, since December 2009 is the extract's opening month and its
"new" customers include anyone who had been buying for years already.
`is_left_censored_cohort` keeps them out of every chart on this page.

</Alert>

## How concentrated the revenue is

Before asking who the customers are, it is worth knowing how few of them carry
the business.

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

The distance between the curve and the dashed line is the whole point. The classic
Pareto shorthand is 80/20; this business is steeper than that, at roughly 77/20 —
and far steeper still at the very top.

Fifty-eight customers out of <Value data={concentration_stats} column=n_customers fmt="#,##0"/> account for just under a third of everything sold, and the bottom half of the base accounts for the last 6.6%.

<Alert status=info>

**So what.** Concentration this steep changes what a retention number is worth.
A campaign that lifts overall repeat rate by two points but misses the top
percentile has moved almost nothing; losing nine of those 58 customers costs more
than losing the bottom 2,900. It also sets the reporting grain: an average order
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

Champions are <Value data={champions} column=pct_customers fmt='0.0"%"'/> of the identified customer base and <Value data={champions} column=pct_revenue fmt='0.0"%"'/> of its revenue, which is most of what the segmentation is for.

<Alert status=info>

**Why this is in Polars and not in SQL.** The operation is cutting a column into
quintiles. SQL's primitive for that is `ntile(5)`, which fills five buckets of
equal size, so a run of tied values gets cut wherever the bucket boundary
happens to land. Frequency is a small integer with heavy ties: 1,626 customers
have placed exactly one order, and `ntile` splits them across quintiles 1 and 2.
Counting the four tied values that straddle a boundary, 3,227 of 5,881 customers
could be scored differently from someone whose behaviour is identical to theirs.

Polars' `qcut` cuts on the break points, so equal values always score equally.
The buckets then come out uneven, which reflects the customer base rather than
the method. A Dagster asset check counts any value carrying more than one score,
since reverting to `ntile` would still produce five tidy buckets and a believable
segment mix.

</Alert>

## Matching returns to sales with no key to join on

18,286 lines reverse a sale, and none of them reference the sale being reversed.
There is no foreign key and no credit-note number, so the link has to be
inferred: for each returned line, take the same customer's most recent earlier
purchase of the same product.

```sql returns
select
    match_status,
    count(*)                                    as n_lines,
    100.0 * count(*) / sum(count(*)) over ()    as pct,
    sum(return_amount_gbp)                      as amount_gbp,
    median(days_to_return)                      as median_days
from warehouse.retail_returns
group by match_status
order by n_lines desc
```

<DataTable data={returns} rows=4>
    <Column id=match_status title="Outcome"/>
    <Column id=n_lines title="Lines" fmt="#,##0"/>
    <Column id=pct title="Share" fmt='0.0"%"' contentType=bar/>
    <Column id=amount_gbp title="Value" fmt='"£"#,##0'/>
    <Column id=median_days title="Median days" fmt="#,##0"/>
</DataTable>

```sql return_timing
select
    median(days_to_return)                             as median_days,
    avg(days_to_return)                                as mean_days,
    count(*) filter (where days_to_return = 0)         as same_day
from warehouse.retail_returns
where days_to_return is not null
```

Each row carries its own outcome rather than being folded into a single accuracy
figure, because the failures have different causes. "No prior purchase in
window" usually means the extract starts in 2009 and the sale happened in 2008,
so the data is simply absent. "Quantity exceeds purchase" means the rule matched
the wrong sale. At 2.0% that second figure is the one worth watching, since it
measures the inference going wrong rather than the source being incomplete.

The timing distribution is a check on whether the matches are real, and it holds up: the median return comes back <Value data={return_timing} column=median_days fmt="#,##0"/> days after purchase, and <Value data={return_timing} column=same_day fmt="#,##0"/> come back the same day. Matches drawn from arbitrary earlier sales would spread evenly across the two-year window.

## The customers who are not here

```sql anonymous
select
    n_lines_anonymous,
    revenue_gbp_anonymous,
    100.0 * n_lines_anonymous / n_lines             as pct_lines,
    100.0 * revenue_gbp_anonymous / revenue_gbp     as pct_revenue
from warehouse.retail_headline
```

<Grid cols=2>
    <BigValue data={anonymous} value=pct_lines title="Lines with no customer id" fmt='0.0"%"'/>
    <BigValue data={anonymous} value=pct_revenue title="…as a share of revenue" fmt='0.0"%"'/>
</Grid>

Every per-customer number on this page, including retention, RFM and average order value, covers only part of the business. <Value data={anonymous} column=revenue_gbp_anonymous fmt='"£"#,##0'/> of revenue comes from orders with no customer id, and none of it can be attributed to anyone.

The two shares differ: anonymous rows are <Value data={anonymous} column=pct_lines fmt='0.0"%"'/> of lines but <Value data={anonymous} column=pct_revenue fmt='0.0"%"'/> of revenue, because orders placed without signing in are smaller ones. Lines are the easier figure to reach for, and using that share in place of the revenue share overstates the gap by nine points.

---

**Sources.** [UCI Machine Learning Repository, Online Retail II](https://archive.ics.uci.edu/dataset/502/online+retail+ii)
(Chen, D., 2019), CC BY 4.0. Exchange rates from the ECB via
[Frankfurter](https://frankfurter.dev). The transaction data is real and
unmodified; the modelling decisions on this page are documented in
`dbt/models/staging/stg_retail_lines.sql` and the five marts built on it.
