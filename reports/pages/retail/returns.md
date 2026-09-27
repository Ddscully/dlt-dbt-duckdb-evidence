---
title: Returns
description: Matching return lines to their sales with no key, and the revenue with no customer attached.
sidebar_position: 5
---

[← Retail Transactions](/retail)

**Return lines carry no reference to the sale they reverse, so the link is inferred; and 14% of revenue has no customer at all.**

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

Each row carries its own outcome instead of being folded into a single accuracy
figure, because the failures have different causes. "No prior purchase in window"
usually means the extract starts in 2009 and the sale happened in 2008, so the
data is simply absent. "Quantity exceeds purchase" means the rule matched the
wrong sale. At 2.0% that second figure is the one to watch: it measures the
inference going wrong, where the first measures the source being incomplete.

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

Every per-customer number in this section, including retention, RFM and average order value, covers only part of the business. <Value data={anonymous} column=revenue_gbp_anonymous fmt='"£"#,##0'/> of revenue comes from orders with no customer id, and none of it can be attributed to anyone.

The two shares differ: anonymous rows are <Value data={anonymous} column=pct_lines fmt='0.0"%"'/> of lines but <Value data={anonymous} column=pct_revenue fmt='0.0"%"'/> of revenue, because orders placed without signing in are smaller ones. Lines are the easier figure to reach for, and using that share in place of the revenue share overstates the gap by nine points.
