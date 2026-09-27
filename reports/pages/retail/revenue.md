---
title: Revenue
description: Which of the source's rows are revenue, and what converting them into three currencies turned up.
sidebar_position: 1
---

[← Retail Transactions](/retail)

**The source has one amount column. Summing it gives a number that looks like revenue and is not, because the same column carries cancellations, postage, bank fees and bad-debt adjustments.**

The source has one amount column and one quantity column. Summing them gives a
number that looks like revenue but isn't, because the same two columns also carry
cancellations, postage, bank fees and bad-debt adjustments.

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

All seventeen combinations in the extract, not just the common few. The rare rows
are the ones that cause trouble:

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
whether a revenue figure means anything, so each one is classified in the model
itself and tested there. A caveat in a README would not have stopped anyone
summing the column.

</Alert>

## Three currencies, and a shop that trades on Sundays

The fact table carries every amount in three currencies, converted at the
[daily ECB fixing](/currency) for the transaction date. Doing that against a
transaction log turned up something the exchange-rate series had never shown by
itself.

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

In total <Value data={fx_carried} column=n_lines_fx_carried fmt="#,##0"/> lines, <Value data={fx_carried} column=pct fmt='0.0"%"'/> of the file, convert on a rate the ECB published on an earlier day, and every one of them falls on a weekend, almost all of them on a Sunday. The business trades on Sundays and barely at all on Saturdays: 139,256 lines against 402. It also closes on the same holidays the euro system does, so no weekday closure ever coincides with an order.

The carry-forward rule and its 7-day cap were written against the FX series
alone, where they filled a gap nothing was querying. With a transaction fact on
top, 13% of the rows depend on them.

```sql monthly_currency
-- A real date on the axis, not the 'YYYY-MM' label: 25 category ticks render as
-- "2...". Series are named by their formatted title, so `seriesColors` keys on
-- "Sterling" and "Euros", not on the column names.
select
    cast(invoice_month || '-01' as date) as month_start,
    sum(revenue_gbp)                     as sterling,
    sum(revenue_eur)                     as euros
from warehouse.retail_daily
group by invoice_month
order by invoice_month
```

<LineChart data={monthly_currency} x=month_start y={['sterling','euros']} title="Monthly revenue, GBP and EUR" yFmt="#,##0" seriesColors={{'Sterling': '#1baf7a', 'Euros': '#eda100'}}/>

Both lines track the same trading, so the distance between them is sterling's
exchange rate and nothing the business did. The [electricity price](/currency)
makes the same point at country grain; here it applies to amounts somebody has
to book.
