---
name: unit-testing-dbt-models
description: The nine dbt models that carry unit tests and what mutating each one proved — the method (break the model against a warehouse copy, record what moves), the fixtures that separate an ordering from its permutations, and the defects the data tests could not see. Use when adding or changing a dbt unit test, judging whether a model's data tests are adequate, or investigating a model that is not reproducible between builds.
---

# Unit testing the models (`dbt/models/**/_unit_tests.yml`)

Twenty-eight unit tests over ten models. They exist because a data test cannot
see a wrong answer that is a legal one, and every one of them was written after
mutating the model and watching its data tests stay green. This file is the
record of those mutations — what moved, what did not, and which fixture shapes
are strong enough to catch them.

The models are listed under *Warehouse schemas* in `CLAUDE.md`; the domain
reasoning behind each is in `compliance-models`, `retail-models` and
`currency-and-calendar`.

## The method

- **The way a test here earns its place is mutation, and the method has two
  traps.** Break the model in a plausible way against a *copy* of the warehouse
  (`WAREHOUSE_PATH` at an absolute path — `just dbt-build` targets the real
  one), run its full data-test suite, and record the number that moves. "Nothing
  went red" is the finding, not the all-clear: across the seven models mutated
  this way — `stg_retail_lines`, `fct_cbam_exposure`, `fct_fx_rates_daily`,
  `fct_fx_rates_periods`, `fct_retail_returns`, `fct_retail_customer_cohorts`,
  `dim_retail_customer` — 38 mutations were run and the data tests caught 5.
  - **Run the unmutated baseline inside every batch.** A mutation that fails to
    *apply* is indistinguishable from a test that caught it, and both happened
    here — a `sed` pattern that spanned a hard wrap matched nothing, and a `cd`
    inside a shell function broke every relative path in the loop. The tell in
    both cases was the baseline row disagreeing with itself.
  - **Restore from a copy, never `git checkout <file>`.** During this work the
    tree is dirty by definition; `git checkout` is a revert to HEAD, not an
    undo, and it destroyed a round of uncommitted edits to three files.

## The ten models, and what mutating each one proved

- **There are twenty-eight unit tests, over ten models, and they exist because a data
  test cannot see a wrong answer that is a legal one.** `dim_date`'s
  `fiscal_quarter` carries `accepted_range 1-4`, which is what caught the
  `/3 + 1` float-division bug at quarter *5*. Change the same expression to `/ 4`
  and every fiscal quarter in the warehouse is wrong while **all 19 data tests on
  the model pass** — measured, not argued. Its three unit tests fail on it.
  `fiscal_year_start_date` and `fiscal_year_end_date` had no test of any kind
  before this.
- **`overrides.vars` is the real reason to unit test this model.**
  `fiscal_year_start_month` is configurable and the warehouse only ever builds
  `4`, so eleven of the twelve policies the model claims to support were untested
  *by construction*. The tests pin April, January (where `fiscal_year` must
  collapse onto `year`) and July (where the boundary falls mid-calendar-year).
  No data test can reach a value the project never builds.
- **`stg_retail_lines` is the other model, and there the blind spot is
  `accepted_values`.** It is two `case` expressions —
  `invoice_type` and `item_type` — and two boolean flags built off them,
  `is_stock_write_off` and `is_revenue_line`. `accepted_values`
  proves an answer is *in* the list, never that it is the right member of it. A
  misclassification moves money between buckets without changing any total, so no
  row-level constraint can see it. Mutated against a warehouse copy, running the
  model's 19 data tests each time: dropping `upper()` from `stock_code` moves net
  revenue by **+GBP 1,702** and all 19 pass; sending `AMAZONFEE` to `product`
  moves it by **-GBP 260,764** and all 19 pass; removing
  `invoice_type <> 'adjustment'` from `is_revenue_line` changes **nothing at all**
  and all 19 pass. Only the fourth mutation goes red — `is_stock_write_off`
  losing its `invoice_type` term takes the flag from 3,457 rows to 22,950 — and
  only as a side effect of `stg_retail_write_offs_are_never_priced`, which
  notices because cancellations carry a price.
  - **The `upper()` number is the one to remember, because its scale is
    invisible.** Five lowercase `m` lines is what the model's comment warns
    about; the cost is actually the vouchers, because **all 100 of them arrive
    lowercase** (`gift_0001_20`, never `GIFT_`). The `voucher` branch is
    reachable *only* through the case fold, so dropping it books the entire
    family as product. The source also sends one bare `GIFT`, which is a product
    — the underscore in the pattern is doing work.
  - **The adjustment clause is the retail equivalent of the eleven unbuilt
    fiscal policies.** No `A` invoice has ever carried a product code, so
    `invoice_type <> 'adjustment'` has never once been the deciding term and
    nothing in the data can reach it. The unit test poses the row the source has
    not sent.
- **`fct_cbam_exposure` is the third, and the hardest of the three to test any
  other way.** It is a table of euro costs with a statutory deadline whose every
  figure is plausible, transcribed from a legal instrument — so there is no
  independent quantity to check the numbers against and its 20 data tests are
  `not_null` and `accepted_range` with bounds that have to be generous, bar the
  one added with the route fix below. What is
  left to test is the *rules*. Mutated against a warehouse copy: resolving the
  fallback **per column instead of per row** changes not one number in the
  warehouse and all 19 pass; **hardcoding the mark-up at 10/20/30** moves the
  fertiliser average from EUR 105.76 to EUR 115.18 a tonne and all 19 pass;
  partitioning `excess_over_cleanest_source` **by product group instead of by
  good** takes the total from 18,989 t to 30,599 t and all 19 pass. Only
  `count(*)` in place of `count(<total>)` in `priced_goods` goes red, on seven
  `not_null`s, because the 875 heading rows it lets through have no price at all.
  It is caught because it is the only one of the four with **no near-miss**:
  `having count(*) > 0` is a tautology over a `group by` (283 goods out, not
  260), so it deletes the filter rather than weakening it. A rule that is binary
  has no plausible wrong answer; the other three do, which is the whole table.
  The unit test is still worth having, because the seven `not_null`s name the
  symptom — 875 nulls in three columns — and it names the heading rows.
  - **`markup_2026_pct` cannot be unit tested and that is the finding.** It is a
    ratio of two doubles, and the warehouse holds three distinct values of it
    that all print as `10.0` — 9.99999999999998578915, 10.00000000000000888178
    and 10.00000000000003197442. A column with no exact value can carry a range
    test and nothing else, which is the real cost of the 2026/1740 correction
    forcing the schedule from measured to asserted. The fixtures pick totals that
    *are* exact under the mark-up (2.5, 5, 10, 20, 40, 80 for 10/20/30%; 50 and
    almost nothing else for the fertilisers' 1%) so the certificate columns can
    be compared at all.
  - **`production_route_code` did not follow the row-level fallback rule, and
    "consistent by luck" was the wrong reading of it.** It was read off the
    country's row while direct, indirect and total came from the fallback — the
    exact split the rule exists to forbid. The *output* was uniformly null on
    all 755 fallen-back rows, which is what made it look benign; the *input* was
    not. 202 of those rows took their tonnages from a fallback row that carries
    a route, and the mart discarded it. The clearest statement of it was six
    rows of grey hydraulic cement holding an identical 1.28 / 0.09 / 1.37: the
    fallback row showed route `A` and the five countries using that same number
    showed blank. "1.37 tCO2e/t with no production route" is a row Annex I does
    not publish. Fixed 2026-08-24; the route now comes off the row the tonnages
    came from.
    - **Nothing moved but the metadata**, which is why it survived. 202 rows
      gained a route, the row count held at 11,665 and the euro total held at
      EUR 2,462,927.40 to the cent. Every `accepted_range` and `not_null` on
      this mart is over a numeric column and the defect lived entirely in a
      VARCHAR that no test named — `_marts.yml` gave it a `data_type` and
      nothing else. A contract pins a column's shape; only a test pins its
      meaning.
    - **The mutation table missed it because a mutation can only break a rule
      that was written down.** All four mutations targeted encoded rules; this
      was a rule the model's own comment stated in prose and the SQL never
      implemented. Read the comments as claims to check, not as documentation.
    - Held now by `dbt_utils.expression_is_true` on the column, which catches
      exactly the 202 rows when the fix is reverted. Its scope sits **in the
      expression** (`… or is_country_specific`) and not in a `config: where:`,
      because `where` makes dbt_utils wrap the model as `dbt_subquery` and the
      correlated reference then has to name that alias instead of the relation.
      `is not distinct from`, not `=`: 553 of the 755 correctly resolve to a
      null route, and `=` is unknown on a null, which `where not(...)` discards
      — the test would pass by not looking.
- **`fct_fx_rates_daily` is the fourth, and it is the one where the data tests
  look adequate and are not.** Fourteen of them: the grain, `rate_source_date <=
  date_day` (no backfill from the future), `rate_source_date = date_day` on
  published rows, positive rates, a non-negative age. **Five mutations, all
  fifteen green on every one.** Widening the cap from 7 days to 30 prices 46
  stale rows; removing the cap prices all 3,359 and leaves the model
  contradicting itself, `is_rate_stale` true beside a usable rate; `<=` to `<`
  costs 2 rows their rate; `is_rate_stale` on `>=` instead of `>` flags 2 rows
  stale *and* priced. Worst is dropping `partition by currency_code` from the
  carry window: **113,479 rows, 29.7% of the table, then quote another
  currency's fixing** — GBP's 0.7094 becomes 0.3718 — and the grain is still
  unique and every date still moves forward, so nothing sees it. Two unit tests
  catch all five.
  - **The existing tests guard direction and provenance, never identity.**
    `rate_source_date <= date_day` is a real test that stops a real bug (a
    default window frame reaching forward), and mixing currencies moves no date
    backwards, so it cannot help. Two independent properties, one asserted.
  - **A fixture for a partitioning bug has to make the partitions disagree.**
    The obvious symmetric setup — both currencies publishing on day 1 — returns
    the right answer with no partition at all, and the test is green forever.
    `fx_daily_never_carries_one_currency_rate_into_another` has BBB publishing
    *later* than AAA on every day AAA must carry, so an unpartitioned window
    hands AAA the wrong number three times out of three.
  - **What the cap refuses is worth a number.** The ISK's last fixing before the
    2008 collapse is 290.00 and the first after the nine-year gap is 125.01; the
    ARS goes 0.89130 to 1.76373 across its 26-day gap. Carrying either forward
    converts at a rate 132% and 98% from the next real quote. That is the
    argument for the cap being 7 rather than generous.
- **`fct_fx_rates_periods` is the fifth, and the only model so far where an
  existing test caught one of the mutations.** 20 data tests; five mutations,
  four of them green on every one. `avg_eur_per_unit` written as
  `1 / avg_units_per_eur` moves USD 2008 from 0.683499 to 0.679923; `max()` in
  place of `arg_max(.., rate_date)` takes USD 2014's period end from 1.2141 to
  1.3953 and **flips the sign of `period_end_vs_avg_pct`, -8.61% to +5.03%**;
  `min()` for `arg_min` puts the wrong `period_start_units_per_eur` on 16,975
  rows (85.9%); and `period_is_complete` on `<` instead of `<=` changes nothing
  at all. Averaging the dense `fct_fx_rates_daily` instead of the published
  fixings is the one that fails, on
  `fx_periods_annual_buckets_cover_every_fixing`.
  - **That test is the model to copy.** It sums `n_published_days` over the year
    buckets and compares it with the row count of `fct_fx_rates_published`, so
    the *shape* of the input is pinned rather than any value. It was written to
    guard the `dim_date` join and it catches an unrelated bug for free, which is
    what a structural assertion buys over a per-column range.
  - **`arg_max(x, rate_date)` against `max(x)` is the slip to watch for here**,
    because on a rising period they agree and the model has four columns of that
    shape. The fixture makes the period peak in the middle and close below where
    it opened, so last, first, max and min are four different numbers.
  - **The reciprocal gap is worst exactly when someone is looking.** 0.07% for
    EUR/USD in calm 2015, 0.52% in 2008 — and **11.9% for the Icelandic krona in
    2008**, which is the number that makes the rule matter rather than the USD
    one. Fixture values are chosen exact in binary floating point: mean of 0.5
    and 0.125 is 0.3125 against one over the mean of 2 and 8, which is 0.2.
  - **`period_is_complete`'s boundary is unreachable and stays that way.** No
    period ends on the series end date, so `<` and `<=` are indistinguishable in
    the warehouse; the branch is live only on the days the last fixing lands on
    a month, quarter, half or year end. `dim_date`'s eleven unbuilt fiscal
    policies and the retail `<> 'adjustment'` clause are the same category, and
    that is now three of the five models.
- **`fct_retail_returns` is the sixth, and unit-testing it turned up that the
  model is not deterministic.** Six mutations, all ten data tests green on
  every one: checking "no prior purchase" before "no customer id" relabels the
  352 unmatchable rows; `>=` for "quantity exceeds purchase" takes matched from
  16,031 to 10,398; `<` for `quantity_is_consistent` takes consistent to 10,404;
  dropping `item_type = 'product'` from `returns` adds **1,207 rows** of
  cancelled postage and fees; the asof `>` costs 2 rows; and dropping
  `quantity > 0` from `purchases` does **nothing at all**, because every stock
  write-off is anonymous and `customer_id is not null` already excludes them.
  - **`accepted_values` on `match_status` is the trap, and it is the same one
    `stg_retail_lines` has.** All four strings stay legal while tens of
    thousands of rows move between them, and no total changes.
  - **`quantity_is_consistent`'s `<=` is worth 5,613 rows — 34% of all
    matches** — because a complete return is the ordinary case, not an edge one.
  - **The model was not reproducible between builds, and it is a different
    mechanism from the float one.** Three consecutive `dbt run` against
    byte-identical sources gave `matched` = 16,031 / 16,032 / 16,030 and
    `sum(original_quantity)` = 637,411 / 636,410 / 636,208. The cause is ties in
    the `asof join`, which picks arbitrarily among rows tied on its inequality
    key: **33,518 groups share a (customer, product, instant)**, covering 70,174
    of 802,716 purchase lines (8.7%), and 604 returns (3.68%) land on one, up to
    20 deep. `dim_retail_customer` had already met this and settled it by
    ranking on `min(invoice_ts)` then `invoice`; `purchases` now does the same
    with a `qualify row_number()` on `(invoice, line_number)`. Three builds now
    agree to the penny. It mattered more here than there — the table ships as
    Parquet in the release and feeds `reports/pages/retail.md`, so figures moved
    between releases with no upstream change.
    - **The tie-break picks one line and deliberately does not sum them, and
      the cost of that is measured.** Of the 604 tied matches, 63 are flagged
      'matched, quantity exceeds purchase' and **56 would be plain matches if
      the tied lines were added up** — the customer did buy that many, across
      two lines of one order. That is 15% of the 366 rows in the bucket the
      model calls its most interesting number, so **that bucket is an upper
      bound on "the rule found the wrong sale", not a count of it.** Summing is
      a re-specification of the matching rule (`original_line_number` would have
      nothing to point at) and belongs in its own decision, not inside a
      determinism fix.
    - **A tie-break fixture has to separate the ordering from its permutations.**
      The first version put the winning row on both the lowest invoice *and* the
      lowest line number, so `order by line_number, invoice` passed it too. It
      now puts the lowest line number on the highest invoice, and all three
      permutations fail.
- **`fct_retail_customer_cohorts` is the seventh, and the two things that define
  the triangle's *edge* were both untested.** Six mutations against its 11 data
  tests: `<=` to `<` on the ragged bound deletes the newest diagonal of every
  cohort (325 rows to 300, taking 615 active customers and GBP 342k with it),
  and `is_complete_period` inverted relabels every still-open period as
  finished — both green. Only two go red, both on
  `fct_retail_cohorts_month_zero_is_full_retention`, which is the shape worth
  copying: it pins a *structural* property (a cohort is defined by its first
  purchase, so month zero is 100% by construction) rather than a value.
  - **The two filters either side of a ratio are separately correct and jointly
    unanchored.** `active_customers` counts on `invoice_type = 'sale' and
    quantity > 0` because it must agree with `dim_retail_customer`; the money
    sums on `is_revenue_line`. So a customer whose only purchase that month was
    postage is active and worth nothing, and
    `revenue_per_active_customer_gbp` is a ratio across two populations — 88 of
    25,598 active customer-months, 0.34%. The structural test anchors one end
    only, which is why the mutation is invisible.
- **`dim_retail_customer` is the eighth, and it is the model the returns fix
  *learned its rule from*.** 11 data tests over 21 columns, every one of them on
  a date, a count or an identifier — not one on money. Eight mutations, **none
  caught**: `min(country)` for `max` relabels 13 customers, the repeat flag on
  `>= 1` makes all 5,881 repeat customers, `n_orders` over every invoice type
  moves it 36,975 to 44,811, and dropping the sign flip takes the mean return
  rate from +5.89% to -5.89%.
  - **Dropping `, invoice` from `first_order_line`'s window makes the model
    non-reproducible**, exactly as `fct_retail_returns`' tied `asof` did. Four
    builds against byte-identical sources gave four different
    `sum(first_order_gbp)`, spread GBP 4,219, against a baseline stable to the
    penny over three runs. 10 customers have two invoices tied on their earliest
    timestamp; worst single swing GBP 1,182.10. The `fct_retail_returns`
    bullet above already cited this model as the *precedent* for that fix — the rule was learned here, applied
    there, unit tested there, and left unpinned here for the whole time.
  - **A test for a non-determinism bug can itself be flaky, and the first
    version was.** With the tie-break deleted DuckDB returns the first-listed
    row 92.5% of the time and the *correct* one by luck 5% (40 trials), so a
    fixture with one tied customer passes a broken model about one run in
    twenty — observed, on the third verification run. The fix is three tied
    customers with the winner listed last, first and in the middle, so no
    positional rule satisfies all three and luck has to strike three times.
    Verified 8/8 red afterwards. **Re-run a mutation several times whenever the
    bug it encodes is itself non-deterministic**; one green is not a survival.
  - **A mutation whose effect another line cancels is not evidence either way.**
    Turning the join to `first_purchase` into a `left join` changed nothing,
    because the inner join to `first_order_value` is fed from the same
    `purchases` CTE and still drops the same 61 customers. It reads as a
    survivor and is not one — it never reached the rule. Score it separately or
    it inflates the "nothing went red" count.
  - **Asserting a column is not pinning it.** The first fixture asserted
    `first_order_date` while every customer in it bought on exactly one day, so
    `min(invoice_date)` and `max` returned the same answer and the mutation
    swapping them passed. A column needs an input where the candidate
    implementations *disagree*, which is the same lesson as the FX partitioning
    fixture and `lake_matches_warehouse`' two drift cases.
- **`fct_country_weather_year` is the ninth, and it is the only one so far where
  the *test's own premise* was the defect.** `_marts.yml` carried a
  `dbt_utils.expression_is_true` under a comment claiming the `(max + min)/2`
  degree-day convention "runs warmer than the mean-based one, never colder", by
  construction, and that this was therefore the one test catching the two totals
  being swapped. The expression was actually `hdd_minmax_total >= 0 and
  hdd_total >= 0` — non-negativity already carried by `not_null` and two
  `accepted_range {min_value: 0}`, and **invariant under the swap it claimed to
  catch**. Swapping the columns in the final SELECT builds cleanly and **all 29
  of `fct_country_weather_year`'s data tests pass**, with DEU 2022 reading
  2170.55 for 2177.70.
  - **The ordering it asserted is false, so the "obvious" fix reddens the
    build.** Over the full archive — 656 rows, 41 capitals x 16 years —
    `hdd_minmax_total` is the *larger* in 253 rows (38.6%) and the smaller in
    403, gaps running -153.0 to +96.2. Whether the midpoint sits above or below
    the true daily mean depends on the day's diurnal shape and both directions
    occur. Encoding the comment as an expression is the one repair to avoid.
  - **So the fixture puts a country on each side of it.** AAA's midpoint total
    lands above its mean-based one, BBB's below, which pins that the columns are
    distinguishable *without* asserting an order between them. This is the
    `fct_fx_rates_daily` partitioning lesson in a new shape: there the symmetric
    fixture was green against a broken model, here a fixture with both gaps
    pointing the same way would quietly license the false claim.
  - **A comment is a claim, and this repo's own rule found it late.**
    `fct_cbam_exposure`'s route defect is filed above as "read the comments as
    claims to check, not as documentation" — that was a rule the SQL never
    implemented. This is the same failure one level up: prose asserting a
    physical relationship, sitting directly on top of a test that did not
    encode it, so nothing could notice the relationship was false. The
    verification is one query, and it was never run.

- **`expect` is full-set equality, so a model that generates its own rows needs a
  fixture file.** `dim_date` expands its bounds to whole calendar years, so any
  mocked `stg_fx_rates` inside one year yields 366 rows and all 366 must be
  listed. They live in `dbt/tests/fixtures/` (dbt's `test-paths`, *not* the
  Python `tests/fixtures/ingest/`) — and `.gitignore` needed
  `!dbt/tests/fixtures/*.csv`, because the blanket `*.csv` there had exactly one
  exception for the seeds. Without it `dbt test` passes locally against files git
  never took and CI fails on a missing fixture. `stg_retail_lines` is 1:1 on its
  input, so its cases are inline `dict` rows and the truth table *is* the
  fixture — the CSV files are the price of a model that generates rows, not of
  unit testing. `format: csv` and `dict` allow a subset of
  *columns*; `format: sql` does not — it fails with `Binder Error: Referenced
  column "date_key" not found`, which is how that was established.
- **The expected values are generated by a different formulation than the one
  under test** — an explicit rotation of the calendar, `[start .. 12, 1 .. start-1]`,
  position = fiscal month. An oracle that reuses the expression it is checking
  passes whatever that expression does.
