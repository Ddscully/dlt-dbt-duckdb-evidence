---
name: currency-and-calendar
description: The ECB euro reference rates and the calendar — dim_date, dim_currency, the fct_fx_rates_* models and reports/pages/currency.md. Use when editing those models, converting a money column between currencies, choosing a spot or average rate, or touching the currencies seed or the incremental FX model.
---

# Currency and the calendar (`dim_date`, the `fx_*` models, `reports/pages/currency.md`)

The ECB's daily euro reference rates, via [Frankfurter](https://frankfurter.dev)
— no key, no quota, the whole 1999-2026 series in one 3.6 MB request. It is the
**first sub-annual grain in the warehouse**, and everything interesting about it
follows from that rather than from the numbers.

### Why this source

- **The source is small on purpose.** The modelling is the point: a date
  dimension, a gap-filled daily series, a spot-vs-average decision and the first
  incremental model. A harder API would have bought nothing.
### Gaps, carry-forward and the currency panel

- **30% of calendar days carry no rate**, and that is the model. 7,066 of 10,078
  days have a fixing; the rest are 2,878 weekend days and 134 weekday TARGET
  closures. `fct_fx_rates_daily` carries the last fixing forward, which is what a
  finance system does and is the same operation as a slowly-changing lookup —
  and `rate_source_date` says which fixing every row is quoting.
- **The carry-forward is capped at 7 days (`fx_max_carry_forward_days`), and the
  bound is measured.** The longest closure the ECB has ever taken is 5 days
  (36 times, the Christmas/New Year runs). What the cap refuses is the two
  *interior* gaps in the whole series, and both are currency crises rather than
  calendars: the Icelandic krona has no rate for 3,341 days from the 2008 banking
  collapse to February 2018, the Argentine peso none for 34 days from the January
  2002 breaking of the dollar peg. Those 3,359 rows keep their place with a null
  rate and `is_rate_stale` set. An uncapped fill would have put a pre-collapse
  krona on nine years of charts.
- **Nothing in the 14 data tests on `fct_fx_rates_daily` can see the cap, and
  five mutations prove it.** They guard the grain, the direction of the carry
  (`rate_source_date <= date_day`) and positivity — all real, none sufficient,
  because a rate carried 8 days or 3,341 days is a well-formed positive number
  with a source date in the past. All five mutations below pass all 15:

  | mutation | effect on the warehouse |
  |---|---|
  | cap 7 -> 30 days | 46 stale rows gain a rate |
  | cap removed | all 3,359 priced; `is_rate_stale` true beside a usable rate |
  | `<=` becomes `<` | 2 rows at exactly the cap lose their rate |
  | `is_rate_stale` on `>=` | 2 rows stale *and* priced |
  | window loses `partition by currency_code` | **113,479 rows (29.7%) quote another currency** |

  - The last is the one to remember: GBP's 0.7094 becomes 0.3718 and the grain
    is still unique, every date still moves forward, every rate is still
    positive. **Direction and identity are independent properties and only
    direction was asserted.**
  - **A fixture for a partitioning bug has to make the partitions disagree.**
    Two currencies publishing on the same day return the right answer with no
    partition at all. The test has BBB publishing *later* than AAA on every day
    AAA must carry.
  - The cap's value is defensible in numbers: ISK 290.00 before the 2008
    collapse against 125.01 after the nine-year gap, ARS 0.89130 against
    1.76373 across its 26-day one — 132% and 98% from the next real quote.
  - Held by two unit tests in `dbt/models/marts/_unit_tests.yml`, which catch
    all five.
- **The currency panel is not fixed, which is why `dim_currency` exists.** 46
  codes have been quoted and 29 still are. Ten stop on the last business day
  before their country adopted the euro (GRD 2000 through BGN 2025), two at a
  redenomination where the money continues under a new code (TRL→TRY at
  1,000,000:1, ROL→RON at 10,000:1 — a chart following the *code* has a cliff in
  2005), and five simply cease. **The `currencies` seed carries the twelve dates
  that are matters of public record and deliberately guesses at none of the
  other five**, and a test checks each one against the series: every asserted
  retirement date is the day after the last published fixing. `is_quoted` is
  false for exactly one row — EUR, which is the base of every quote and never a
  quote itself.
  - **A hand-maintained seed needs a test in *both* directions, and only one of
    them existed.** `dim_currency` is `from seed left join` the series, so it can
    only ever hold seed rows — and `fct_fx_rates_daily` inner-joins it. A
    currency the ECB starts quoting that nobody adds to the seed therefore
    vanishes from the dense table while still appearing in
    `fct_fx_rates_published` and `fct_fx_rates_periods`: a silent per-currency
    hole, not a build failure, and the shape that makes it hard to spot is that
    only *one* of the three tables is wrong. A `relationships` test on
    `stg_fx_rates.quote_currency` closes it. The reverse — a seed row the series
    never quoted — could not be caught by the `retired_on` test either: its
    subquery returns NULL for a code with no rates, and `retired_on > NULL` is
    null rather than false, so the row passed by being *unknown*. Hence the
    `is_quoted` assertion: 47 rows, 46 quoted, EUR the one exception.
### Both directions, and spot against average

- **`fct_fx_rates_periods` has 20 data tests and four of five mutations pass
  every one of them.** `avg_eur_per_unit` as `1 / avg_units_per_eur` (USD 2008
  0.683499 -> 0.679923), `max()` for `arg_max(.., rate_date)` (USD 2014's period
  end 1.2141 -> 1.3953, and `period_end_vs_avg_pct` **flips sign**, -8.61% ->
  +5.03%), `min()` for `arg_min` (85.9% of `period_start_units_per_eur` wrong),
  and `period_is_complete` on `<` (nothing moves — no period ends on the series
  end date). The fifth, averaging the dense daily table, is caught by
  `fx_periods_annual_buckets_cover_every_fixing`, which pins the input's shape
  rather than any value and catches this for free. Three unit tests in
  `dbt/models/marts/_unit_tests.yml` hold all five.
  - **The reciprocal gap's headline number should be the krona, not the
    dollar.** 0.07% for EUR/USD in 2015 and 0.52% in 2008 read as rounding; the
    ISK in 2008 is **11.9%**. The gap scales with intra-period movement, so it
    is largest in exactly the periods someone is investigating.
- **Both directions of every rate ship.** `units_per_eur` is the ECB's own quote,
  `eur_per_unit` its reciprocal. Same argument as the Scope 2 factor in g/kWh
  *and* t/MWh: a consumer forced to invert it themselves will eventually forget.
- **Spot or average is a real decision and the model refuses to make it.** Stocks
  (a balance at an instant) convert at the closing rate; flows (revenue, spend, a
  price over a period) at the period average. `period_end_vs_avg_pct` measures
  the cost of choosing wrong — +11.7% for EUR/USD in 2003, -8.6% in 2014, +98%
  for the krona in 2008.
  - **Average over published fixings, never over the dense table.** Averaging
    `fct_fx_rates_daily` counts every Friday three times and four or five times
    around a holiday, weighting the mean toward whichever weekday sits next to a
    closure. `fct_fx_rates_periods` reads `fct_fx_rates_published` for that
    reason alone.
  - **`avg_eur_per_unit` is not `1 / avg_units_per_eur`** — the mean of
    reciprocals is not the reciprocal of the mean. 0.07% apart in a calm year,
    0.53% in 2008. Each column is the mean of its own series; the period-*end*
    columns invert exactly, because a single point has no averaging in it.
### `dim_date` is a calendar, not a market calendar

- **`dim_date` is a calendar and not a market calendar.** It knows weekends; it
  does not know trading days in any jurisdiction, and the TARGET closures are
  observed as absences rather than asserted from a list that would need
  maintaining forever. Two traps it exists to stop:
  - **ISO year is not calendar year.** 2021-01-01 is a Friday in ISO week 53 of
    ISO year *2020*, and 2019-12-30 is already in week 1 of 2020. Grouping by
    `(year, iso_week)` splits one week over two buckets. Pair `iso_year` with
    `iso_week`, or group on `iso_week_start_date`.
  - **`/` is float division in DuckDB, and `cast(... as integer)` rounds.** The
    fiscal-quarter expression `((month - start + 12) % 12) / 3 + 1` gives 4.67
    for March under an April year start, which cast to an integer is **quarter
    5**. `floor()` is the fix, and the test that caught it is an
    `accepted_range` of 1-4.
  - The fiscal columns come from the `fiscal_year_start_month` var (4 = April)
    and **the value used is carried on every row**, because the same Tuesday is
    in a different fiscal year on someone else's books. `fiscal_year` is the year
    the fiscal year *ends* in, which is what makes it collapse onto `year` when
    the var is 1.
### The one incremental model

- **`fct_fx_rates_published` is the only `materialized='incremental'` model in
  the project, and it is the right one rather than the biggest one.** Every other
  table here re-derives a source that gets fully re-fetched, so rebuilding it is
  how a restatement is picked up, not waste. This one grows ~30 rows a day
  forever and a published fixing never changes. Numbers, honestly: incremental
  0.16 s against 0.24 s full-refresh at 265k rows — the saving is 0.08 s and the
  argument is the shape of the curve, not today's seconds.
  - **`delete+insert` on the grain, not `append`.** Ingestion re-asks for a
    lookback window and an append would duplicate every row in it. Same
    idempotence argument as `wb_wdi`'s merge key one layer up.
  - **Two lookback windows, deliberately not equal.**
    `fx_incremental_lookback_days` (30) must be **no smaller** than
    `FX_LOOKBACK_DAYS` (10) in `ingest/pipeline.py`. Two constants that have to
    match are a drift bug; one that only has to be no smaller costs a few rows a
    run and cannot fail in the direction that loses data.
  - A `dbt_utils.equal_rowcount` against `stg_fx_rates` is the guard. It only
    works because this model is a faithful copy of the view — keep it that way,
    or that one cheap test stops meaning anything.
- **The FX watermark is one value for the table, where WDI's is one per
  indicator.** Every currency comes back in the *same* request, so a newly listed
  one is covered by the table-wide high-water mark. WDI needs the per-indicator
  form precisely because adding an indicator adds a request that has never been
  made before. Same mechanism, opposite answer, for a reason worth keeping
  straight.
### Fixtures, and what it changed elsewhere

- **The FX fixture is the whole series, gzipped** (3.6 MB → 843 kB), and it is
  the one fixture that isn't trimmed. Every discontinuity above is something a
  model is tested against, so cutting the date range would take the euro
  changeovers, the rouble and Iceland out of CI. It is also the reason `_get_json`
  has a `.gz` branch.
- **The one thing it changes about a number already on the site.** Eurostat's
  household electricity price is the warehouse's only euro-denominated
  measurement, sitting beside the World Bank's dollar GDP. Converted at the
  half-year average, the 39 countries present in both halves rose **35%** from
  2021-S1 to 2022-S2 in euros and **13.5%** in dollars, because the euro fell
  from 1.205 to 1.014 over the same eighteen months. Neither is wrong; a chart of
  "European electricity prices" with no stated currency is reporting the exchange
  rate as if it were an energy market. That is the `gdp_usd` vs
  `gdp_constant_usd` gotcha below, finally measured instead of narrated.
- **`marts.fct_fx_rates_daily` is archived to the lake and `raw.ecb_fx_rates` is
  not** — the reverse of every other table there. The landing table is keyed on
  `rate_date` and has no `year` to partition on. It is also the only table that
  improves the archive's small-file arithmetic: 381k rows over 28 partitions.
