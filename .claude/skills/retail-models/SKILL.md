---
name: retail-models
description: The dbt retail group — fct_retail_order_line, the retail dimensions, cohorts, returns and analytics.retail_rfm, built on UCI Online Retail II. Use when editing any retail_* model, the RFM Polars transform or reports/pages/retail.md, and before trusting a revenue, return or retention number out of them.
---

# Retail transactions (the `retail_*` models, `analytics.retail_rfm`, `reports/pages/retail.md`)

[UCI Online Retail II](https://archive.ics.uci.edu/dataset/502/online+retail+ii)
— a UK gift wholesaler's complete transaction log, 1,067,371 lines over
2009-12-01 to 2011-12-09. **The first grain below a country**, the first source
that is a bulk file drop rather than an API, and the first fact recording what a
person did rather than what an agency published. Six dbt models plus a Polars
one; the page is `retail.md`.

### The mess is the deliverable

- **The mess is the deliverable.** Nothing about this source has been cleaned by
  anyone, so the modelling *is* the value — and each decision has a wrong answer
  that produces a plausible number. The three that matter, all measured:
  - **A negative quantity is not a return.** 3,457 negative lines sit on *sale*
    invoices, every one priced at exactly zero with no customer: inventory
    write-offs (damage, stock counts, one row labelled `check`). Reading them as
    returns inflates the return count by a fifth and the returned value by
    nothing — an error that survives review because the money still balances.
    `is_stock_write_off` and a test hold it.
  - **There are three invoice prefixes, not two.** Beside the `C` cancellations
    sit six `A` bad-debt adjustments worth −£147,614, and they are the only
    negative *prices* in the file.
  - **A returned quantity is positive everywhere, and for a while it wasn't.**
    `fct_retail_returns` negates the source's sign on purpose (4 reads better
    than −4); `dim_retail_product.units_returned` kept the raw negative until
    2026-08-18, so the two models disagreed about which way a return points.
    Nothing failed — the column had a `data_type` and no description — but the
    one place a reader would put them together, `units_returned / units_sold`,
    came out negative, and a bar of returns per product drew below the axis. It
    is 1..80,995 now, with an `accepted_range` holding it. A sign convention that
    is only written down in one of the two models that use it isn't one.
  - **`item_type` is not decoration.** A bare revenue sum carries £463,931 of
    postage and −£338,803 of bank fees as if they were sales.
### Returns, customers and cohorts

- **Returns have no foreign key**, so `fct_retail_returns` infers the link with
  an `asof left join` to the same customer's most recent earlier purchase of the
  same product. 87.6% match cleanly, 2.0% match a *smaller* purchase, 8.4% have
  no prior purchase in the window, 1.9% have no customer id. **The 2.0% is the
  interesting number**, not the 87.6% — it is the rule being wrong rather than
  the data being absent. Reported per row instead of tuned into one headline; the
  median return comes back in 10 days, which is the evidence the rule isn't
  latching onto arbitrary sales.
- **`dim_retail_customer` covers a subset of the business and says so.** 22.8%
  of lines have no customer id — £2.67M, 13.8% of revenue. The two shares differ
  because an order nobody signed in for is a smaller order, and quoting the line
  share as the revenue share overstates the hole by nine points. Also: 5,881 of
  the 5,942 ids reach the dimension (61 never purchased), and `cohort_month` is
  the first *purchase*, not the first appearance.
- **The cohort triangle is ragged and left-censored, and both are columns.** A
  cohort born in November 2011 has no month-12 row — that is an absence, not a
  zero, so rows are generated only up to the last observable month.
  December 2009 is the extract's first month, so its "new" customers include
  everyone already buying; `is_left_censored_cohort` excludes them everywhere.
  Retention is against the cohort's own size, never against the previous month.
  - **Read a triangle by direction: down a column is ageing, along a diagonal is
    the calendar.** The heatmap's dark band is a diagonal — autumn. Pooled over
    every cohort age, a customer is active in 23.6% of September–November months
    against 15.3% for the rest of the year (Jan 11.2%, Nov 27.0%). The month-12
    "recovery" in the average curve is the same fact edge-on, since a cohort's
    twelfth month lands in the calendar month it was born in. A retention *curve*
    averages the diagonal into the column and reports the blend as ageing.
  - `retail_max_cohort_age_months` (36) is a var, not a literal, because it is
    the one number that can silently **truncate** the answer.
    `fct_retail_cohorts_are_not_truncated` asserts the first cohort reaches the
    last month.
### The Polars RFM layer

- **`analytics.retail_rfm` is where the Polars layer stops being a division.**
  The operation is "cut a column into quintiles", and SQL's primitive for it —
  `ntile(5)` — is *wrong*: it fills buckets of equal size, so it cuts through a
  run of equal values wherever the boundary lands. 1,626 customers have placed
  exactly one order, and across the four tied values that straddle a boundary
  **3,227 of 5,881 customers** could be scored differently from someone whose
  behaviour is identical. `qcut` cuts on break points, so equal values always
  score equally and the buckets come out uneven — which is a fact about the
  customer base, not an artefact. `rfm_scores_do_not_split_ties` is a blocking
  asset check, because a regression to `ntile` still yields five tidy buckets and
  a plausible segment mix.
  - **Casting a Polars Categorical straight to an integer gives the physical
    dictionary index**, i.e. order of first appearance, not label order. Via
    `String` is the only reading that means what it says.
  - **Monetary is null for 28 customers, and the check now says so rather than
    not looking.** `monetary_gbp` is `net_revenue_gbp`, which `dim_retail_customer`
    already publishes as null for the customers whose orders held no revenue
    line — so `qcut` returns null and `concat_str`/`+` propagate it into
    `rfm_cell` and `rfm_total`. Kept null rather than coalesced to 0: a 0 scores
    them into the bottom quintile, which reads as "measured, worth nothing"
    instead of "nothing to measure". `segment` is unaffected, because the grid is
    R and F only. Two things this cost: `rfm_scores_do_not_split_ties` tested
    only `segment is null` and so could not see any of it, and Polars sorts nulls
    **first**, so a descending sort opened the table with the 28 least
    informative rows in the file. `nulls_last=True`, and the check now asserts
    the nulls fall exactly where monetary is null and nowhere else.
  - **`as_of_date` is a required parameter with no default.** Recency against
    `date.today()` makes every customer in a 2011 extract equally and
    enormously lapsed, and the segmentation quietly becomes a frequency ranking.
    It is read from the data and shipped as a column.
  - **The segment map is a 25-cell grid, not a rule list.** The widely-copied
    version ("Champions: R>=4 and F>=4", "Loyal: R>=3 and F>=3", …) has
    *overlapping* conditions, so the label depends on branch order — invisible in
    review. Monetary is deliberately not in the grid: R and F say what the
    relationship is doing, M says what it is worth.
  - Champions are 14.9% of the identified base and 62.7% of its revenue.
### First-order value against lifetime value

- **`dim_retail_customer.first_order_gbp` is the page's only forward-looking
  number, and the statistic that describes it is not the obvious one.** Pearson
  *r* against lifetime value is 0.641 and is almost entirely one customer: drop
  the single largest first order (£33,168 → £235,833) and it falls to **0.397**;
  under £5,000 it is 0.344. The **rank** correlation does not move — 0.592,
  0.592, 0.590 across the same three cuts — so that is what the page quotes,
  with the quintile medians (£191 → £410 → £714 → £905 → £1,885, repeat rate
  58% → 78%) as what it means in money. A Pearson *r* on a heavy-tailed money
  column is a statistic about the tail.
  - **An invoice, not a day.** `n_orders` counts invoices, so "order" has to
    mean the same thing in both columns — and 393 of the 5,881 customers bought
    twice on the day they arrived, so the choice moves the number. Ranking is on
    `min(invoice_ts)` (83 invoices carry more than one timestamp) then on
    `invoice`, because 11 customers opened two at the same minute and a
    non-deterministic tie-break is a column that changes between builds.
  - **Null for the 47 whose first invoice held no product line** — a `Manual`
    adjustment or the test SKU. Same `filter (where is_revenue_line)` shape as
    `net_revenue_gbp`, which is already null for 28 customers, so this is the
    model's existing convention rather than a new one.
  - **`first_order_gbp <= net_revenue_gbp` is true by construction and fails as
    a test, on 272 rows.** Every one is a one-order customer where the two
    columns are the same money summed in a different order; the excess tops out
    at 1.8e-12. Comparing two independently-summed doubles for containment is a
    float-equality test wearing an inequality. The shipped test is
    `accepted_range {min_value: 0}`, which is the same guarantee without the
    arithmetic.
  - **The chart cost four Evidence gotchas**, all written up in
    `reports/README.md`: a scatter over ECharts' `progressiveThreshold: 3000`
    never finishes rendering, `yLog=true` in markdown is the *string* `"true"`
    and leaves the axis linear, there is no `xLog` prop at all, and `> 0` is not
    a safe filter for a log axis when two customers carry 1e-14 of float residue.
  - `ntile(5)` is correct for the quintile table and wrong for the RFM scoring
    two sections below it, which is worth reading as a pair. A near-continuous
    currency column has almost no ties to split; `frequency` has 1,626 customers
    on one value.
### Where retail touches the rest of the warehouse

- **The FX carry-forward stops being theoretical here.** 139,658 lines (13.1%)
  convert on a rate the ECB published earlier, and **every one is a Sunday**:
  this business trades Sunday and not Saturday (139,256 lines against 402), and
  it closes on exactly the days TARGET does, so no weekday closure ever
  coincides with an order. A model assuming "weekend" means Saturday and Sunday
  equally reads it backwards.
- **`fct_retail_order_line` carries a plain `year` beside `iso_year`**, because
  the lake partitions on it. The two agree on every row *only* because the
  business shuts 23 December to 4 January, so nothing lands on the three days a
  year where ISO week 1 crosses the new year. Partitioning on `iso_year` would be
  correct today and wrong the first New Year they trade.
- **It is the only table whose lake partitioning is sensible**: 1.07M rows over
  three years is three files of 13 MB / 12 MB / 836 kB, against the CO2
  archive's 275 files averaging 47 kB. The grain is a transaction and the
  partition is a year, so the ratio is 350,000:1 rather than 150:1.
- **The fixture is selected by shape, not sampled.** A 4% random draw keeps the
  volume and loses all six `A` adjustments and the single positive `C` line.
  `RETAIL_FIXTURE_SELECTION` in `scripts/record_fixtures.py` picks each shape
  explicitly and `tests/test_fixtures.py` asserts they survive. At 1.88 MB it is
  the largest file in the repo.
