# 03 — Tests that fail on bugs, not on reality

← [02 — Loading twice](./02-loading-twice.md) · [Course index](./README.md) · next: [04 — Denominators, units and coverage](./04-denominators.md)

**Objectives.** Calibrate a bound against a measured distribution instead of an
intuition. Say, for any test in this repo, exactly which rows it examines and
which it cannot see. Explain why a test can pass because its expression is
*unknown* rather than true, and read a failure out of `dbt_test__audit` instead
of out of a log.

**Prerequisites.** A built sandbox ([00](./00-setup.md)). Module
[01](./01-grain.md) for why absence is the hard case, and [02](./02-loading-twice.md)
for the pivot that turns row loss into column emptiness.

---

## 1. The census

A dbt test is a `select` that must return no rows. That is the whole mechanism,
and everything below is a consequence of it. This project has 425 of them:

| test | n | what it asserts |
|---|---|---|
| `not_null` | 194 | this column is populated on every row |
| `dbt_utils.accepted_range` | 143 | this column's values lie in a band |
| `dbt_utils.expression_is_true` | 30 | an arbitrary row-level claim |
| `dbt_utils.unique_combination_of_columns` | 28 | this is the grain |
| `accepted_values` | 12 | this column is an enum |
| `unique` | 11 | this key does not repeat |
| `relationships` | 5 | every value here exists over there |
| `dbt_utils.equal_rowcount` | 2 | these two relations are the same height |

```bash
just course-query "
select test_type, count(*) as n from analytics.pipeline_tests
group by 1 order by 2 desc"
```

Read the shape of that table rather than the total. **337 of the 425 (79%) are
`not_null` and `accepted_range`**, both of which are per-column statements about
rows that are *present*. That is module 01's punchline restated as a census: the
test suite is overwhelmingly made of assertions that a deletion makes *more*
likely to pass.

The three at the bottom are the interesting ones, and they are rare because they
are the only ones that compare a relation against something outside itself.

## 2. A bound is a claim about a distribution

`{min_value: 0}` on a tonnage column is free: no physical quantity of CO2 is
negative, and the bound is a statement about the unit rather than about the data.
A *ceiling* is never free. It is a claim that you know the shape of the tail.

Here is the repo making that claim, in `dbt/models/staging/_staging.yml`, out
loud:

```yaml
- name: carbon_intensity_elec_g_kwh
  description: >
    …Ceiling is generous: the observed maximum is ~1307 (coal-only grids),
    so 2000 catches a unit error without failing on a real grid.
  data_tests:
    - dbt_utils.accepted_range:
        arguments: {min_value: 0, max_value: 2000}
```

Two things make that a calibrated bound rather than a hopeful one. The maximum
was **measured**, and the ceiling is set at a multiple of it: far enough above
reality that no real grid reaches it, close enough that a factor-of-1000 unit
error (g/kWh read as mg/kWh) cannot hide underneath it. The number a test should
catch is not "slightly too big"; it is "wrong by an order of magnitude", because
that is what a unit error, a column read from the wrong position, or a
misplaced decimal actually looks like.

And here is the same file **refusing** to bound a column:

```yaml
- name: trade_co2_share
  description: >
    `trade_co2` as a percent of territorial emissions. Range is roughly
    -98% to +1023% in the full data — Singapore imports ten times what it
    emits — so this is deliberately untested rather than bounded to
    0-100. A bound here would fail on reality, not on a bug.
```

Measured: 4,063 non-null rows, **1,268 of them negative and 268 above 100%**,
topping out at Malta in 2016 on 1,023%. A `0–100` bound (the reflex for anything
named `_share` or `_pct`) would fail on 1,536 rows, every one of them correct.
A test that fires on real data does not get fixed. It gets muted, and it takes
the credibility of the other 424 with it.

> The rule this section is built on: **write the bound after you have looked at
> the distribution, and write down what you saw.** The description field is where
> the evidence goes, because the next person to tighten the bound needs to know
> it was already loosened on purpose.

## 3. Three ways a row escapes a test

This is the part that is not in the dbt documentation, and it is the whole reason
425 tests is not the reassuring number it looks like.

**(a) The value is null.** `dbt_utils.accepted_range` compiles to:

```sql
select * from model
where 1 = 2
   or not column >= 0
   or not column <= 100
```

`not (null >= 0)` is `null`, not `true`, so the row is not selected and the test
passes. Every `accepted_range` in this repo silently skips its nulls, which is
correct behaviour (a missing value is not an out-of-range one) and is also why a
range test alone says nothing about coverage. Pair it with `not_null` when the
column really is mandatory; leave it unpaired, deliberately, when the column is
legitimately sparse.

**(b) The test is scoped with `where:`.** A `config: {where: …}` narrows the rows
the test runs over, and several of this project's most important tests use it:

| test | rows in the relation | rows examined |
|---|---|---|
| `dim_grid_emission_factors` grain, `where: is_latest_available` | 5,561 | **207** |
| `stg_retail_lines` write-off check, `where: is_stock_write_off` | 1,067,371 | **3,457** |
| `fct_retail_returns` match check, `where: is_matched` | 18,286 | 16,398 |
| `cbam_default_values` sum check, `where:` all three columns non-null | 12,540 | **2,781** |

None of those is a weakened test. `dim_grid_emission_factors` is one row per
country *per vintage year*; the claim "one row per country" is only true on the
latest-available slice, so scoping it there is what makes it a true statement
rather than a failing one. But the CBAM row is the one to sit with: that test
reads `direct + indirect = total`, and `indirect` is published for cement,
fertilisers and 34 iron-and-steel rows only. **8,129 rows carry a direct and a
total with nothing whatsoever checking them.** The test is right, and "the annex
is checked against itself" is a sentence that would badly overstate it.

**(c) The rows are not there.** Covered in module 01 and unfixable by any test in
the table above. Worth one line here: of the eight test types, exactly one
(`equal_rowcount`) can fail because rows are *missing*, and it works only because
it compares against another relation.

Measure (a) for yourself on the wide fact, where it is starkest:

```bash
just course-query "
select count(*) as mart_rows,
       count(electricity_price_eur_kwh) as price_examined,
       count(co2_mt) as co2_examined,
       count(year) as year_examined
from marts.fct_emissions_energy"
```

## 4. What a failure hands you

`dbt/dbt_project.yml` sets `data_tests: +store_failures: true` project-wide, so
every test writes the rows it rejected into a `dbt_test__audit` schema. A red
build in CI or Dagster is then a table you can query, not a count you have to
reproduce locally:

```sql
select * from dbt_test__audit.<test_name>;
```

Two consequences that are not obvious:

- **An empty audit table is a passing test, so `count(*)` is not the verdict.**
  A test's real verdict is its `fail_calc`, which defaults to `count(*)` but does
  not have to be. `dbt_utils.equal_rowcount` overrides it with
  `sum(coalesce(diff_count, 0))` and writes a one-row *summary* whether it passed
  or failed: `(1, 1, 265035, 265035, 0)`. Counting rows scores that as one
  failure against a build that finished `ERROR=0`, which is how the pipeline
  health page came to contradict the build it was reporting on.
  `src/modern_data_stack/observability.py` reads `fail_calc` out of the manifest
  and applies it; 393 of the 425 tests here use the default.
- **dbt writes that schema every build and never cleans it.** An audit table
  whose test has been renamed or deleted stays, is empty, and therefore scores as
  passing. A real warehouse here held **391 audit tables, 22 of them orphans**
  whose tests no longer existed. `transform/pipeline_status.py` filters them
  against the manifest for that reason.

---

## 🔧 Drill 1 — the ceiling that seventeen countries agree with

**Symptom.** A reviewer notices that `co2_per_capita` has a floor and no ceiling,
and that this looks like an oversight next to the other 122 range tests. They
check the data (the highest value anywhere is 22.2 t/person) and set a ceiling
of 50, which is more than double the observed maximum. The build is green.

**Seed the bug.**

```bash
sed -i '/name: co2_per_capita/,+3 s/{min_value: 0}/{min_value: 0, max_value: 50}/' \
  dbt/models/staging/_staging.yml
just course-rebuild
```

**Observe.** `PASS=402 WARN=0 ERROR=0 SKIP=0`: byte-identical to healthy. And
the reviewer's evidence checks out:

```bash
just course-query "
select count(*) as rows, round(max(co2_per_capita), 2) as max_observed
from staging.stg_co2"
```

**Your task.** The change is wrong. Prove it *without* rebuilding the real
warehouse.

1. Query `data/warehouse.duckdb` for the rows this test would have rejected. How
   many are there, and how many countries?
2. Look at those rows. Is any one of them bad data? Say what each country was
   doing.
3. **The question the drill is for:** the reviewer could have caught this by
   checking against the real warehouse instead of the sandbox. Find the *second*
   dimension along which their check would still have been wrong, even run
   against the full 213 countries.
4. What ceiling, if any, would you ship? Defend it in one sentence that a future
   reviewer will read before tightening it.

**Verification.**

```bash
git checkout dbt/models/staging/_staging.yml
just course-rebuild
just course-query "
select count(*) as rows, round(max(co2_per_capita), 2) as max_cpc
from staging.stg_co2"
```

Healthy: `3487`, `22.22`.

<details>
<summary>Reveal</summary>

**1. 124 rows, across 6 countries.**

```sql
select s.country_iso3, c.country_name, count(*) as rows_over_50,
       min(s.year) as first_year, max(s.year) as last_year,
       round(max(s.co2_per_capita), 1) as peak
from staging.stg_co2 s join staging.stg_country c using (country_iso3)
where not s.co2_per_capita <= 50
group by 1, 2 order by rows_over_50 desc;
```

| iso3 | country | rows | years | peak t/person |
|---|---|---|---|---|
| SXM | Sint Maarten | 35 | 1950–1985 | **782.7** |
| CUW | Curaçao | 35 | 1950–1985 | 117.3 |
| QAT | Qatar | 29 | 1963–2006 | 121.7 |
| BRN | Brunei | 11 | 1939–1978 | 245.1 |
| ARE | United Arab Emirates | 10 | 1969–1978 | 85.6 |
| KWT | Kuwait | 4 | 1964–1991 | 364.8 |

**2. None of it is bad data.** Every one is a refinery or oil economy with a
population of a few tens of thousands. Sint Maarten and Curaçao hosted the
Isla refinery complex on a combined population under 200,000; Brunei, Kuwait,
Qatar and the UAE are pre-diversification petrostates. The denominator is what
makes the number look impossible, and the number is right.

The sandbox's own maximum is the other half of the lesson: **22.22, the United
States in 1973**: peak American per-capita emissions, the year of the oil
shock. It is a real and satisfying maximum, and it is 35× too small.

**3. The second dimension is time.** 117 of the 124 rows are before 2000, **7
are after it, and none at all since 2020.** So a reviewer who did the right
thing (pulled the full 213-country warehouse and checked the recent
cross-section) would have found a maximum around 40 and shipped the same
ceiling. It would then have passed every build until the first person ran
`just ingest-wdi-full`, restated an old year, or simply looked at 1954.

A bound is calibrated against **every row the model will ever hold**, and a
warehouse with history has two axes to be wrong along. The 17-country fixture
slice gets you on the first; "current data looks fine" gets you on the second.
This is the exact mirror of module 02's shape test, which held at fixture scale
and not at production scale.

**4. There is a defensible ceiling and it is nowhere near 50.** Something like
`max_value: 1000` still catches the failures a ceiling exists for — a
tonnes/kilograms mix-up, a per-capita column populated with the absolute — while
sitting 28% above Sint Maarten in 1954. Or ship no ceiling at all, which is what
the repo does, and say why in the description. Both are honest. What is not
honest is a bound whose only justification is that today's data happens to fit
under it.
</details>

---

## 🔧 Drill 2 — the seed row that passes by being unknown

**Symptom.** Someone adds a currency to `dbt/seeds/currencies.csv`. They fill in
every column carefully, including a retirement date and a successor. The code
does not exist.

`dbt/seeds/_seeds.yml` carries a test on that seed which looks like it was
written for exactly this:

```yaml
- dbt_utils.expression_is_true:
    arguments:
      expression: >
        retired_on is null
        or retired_on > (
            select max(rate_date) from {{ ref('stg_fx_rates') }} as r
            where r.quote_currency = currency_code
        )
```

**Seed the bug.**

```bash
echo 'XYZ,Fictional Dollar,2015-01-01,euro_adoption,EUR' >> dbt/seeds/currencies.csv
just course-rebuild
```

**Your task.**

1. `ERROR=1`. Which test failed, and, more importantly, which test *did not*?
2. Evaluate the seed's own expression for `XYZ` by hand. It is not returning
   `false`. What is it returning, and why does that let the row through?
3. Now do it in the other direction: restore the seed, delete a currency the ECB
   really does quote, and rebuild. A different test fires. Explain why neither
   test can do the other one's job.
4. Suppose only the seed's own test existed. Name the table that would be wrong,
   the two tables that would still be right, and say why that combination is the
   hardest possible shape to notice.

**Verification.**

```bash
git checkout dbt/seeds/currencies.csv
just course-rebuild
just course-query 'select count(*) as rows from marts.dim_currency'
```

Healthy: `47`.

<details>
<summary>Reveal</summary>

**1.** The failure is on the *mart*, not the seed:

```
Failure in test dbt_utils_expression_is_true_dim_currency_currency_code_EUR_
  Got 1 result, configured to fail if != 0
```

All **seven** of the seed's own tests pass: including the `retired_on` test that
appears to be about precisely this, and including the `relationships` test
between `stg_fx_rates` and `currencies`.

**2. The expression evaluates to `NULL`.** Run it and look:

```sql
select currency_code, retired_on,
       (select max(rate_date) from staging.stg_fx_rates r
          where r.quote_currency = cur.currency_code) as last_quote,
       retired_on > (select max(rate_date) from staging.stg_fx_rates r
          where r.quote_currency = cur.currency_code) as test_expression
from main.currencies cur where currency_code in ('XYZ', 'GRD', 'USD');
```

| code | retired_on | last_quote | expression |
|---|---|---|---|
| USD | NULL | 2026-08-07 | NULL |
| GRD | 2001-01-01 | 2000-12-29 | **true** |
| XYZ | 2015-01-01 | **NULL** | **NULL** |

`XYZ` has no rates, so `max(rate_date)` is null, so `retired_on > null` is null.
`expression_is_true` selects rows `where not (expression)`, and `not null` is
null, so the row is not selected and the test passes. USD's expression is null
too, but the `retired_on is null or …` short-circuits it to `true`, which is
why the bug hides: the guard clause that makes the test correct for current
currencies is also what stops anyone noticing that the comparison itself is
null-blind.

**This is the single most transferable fact in the module.** A SQL test does not
ask "is this row true?", it asks "can I prove this row false?", and three-valued
logic means *unknown* is scored as innocent. Anywhere a test expression touches a
correlated subquery, an outer join, or a nullable column, ask what it returns
when the thing on the other side is absent. Absence is the case the test was
written for and the case it silently forgives.

The test that does catch it makes no comparison at all:

```yaml
- dbt_utils.expression_is_true:
    arguments: {expression: "currency_code = 'EUR'"}
    config: {where: "not is_quoted"}
```

"A currency in this seed that the ECB has never quoted must be EUR." No
subquery, no null, no escape.

**3. The other direction.** Delete a real currency:

```bash
sed -i '/^PLN,/d' dbt/seeds/currencies.csv && just course-rebuild
```

```
Failure in test relationships_stg_fx_rates_quote_currency__currency_code__ref_currencies_
  Got 7066 results, configured to fail if != 0
```

Every PLN fixing in the series. The `relationships` test runs from
`stg_fx_rates` to the seed — series first, seed second — so it sees a quoted
currency with no seed row. It cannot see a seed row with no quotes, because
nothing on the series side generates a row to check.

**Neither test can do the other's job because they scan different relations.** A
hand-maintained reference table needs an assertion in *both* directions, and
this is the general form: the direction you write first is the one you were
worried about, and the other one is where the bug goes.

**4. `marts.fct_fx_rates_daily` alone would be wrong.** It builds its date spine
from `dim_currency where is_quoted` and inner-joins, so a currency missing from
the seed has no spine and no rows. `fct_fx_rates_published` and
`fct_fx_rates_periods` read `stg_fx_rates` and are untouched. Against the real
warehouse, PLN is:

| table | rows |
|---|---|
| `fct_fx_rates_published` | 7,066 |
| `fct_fx_rates_daily` | **10,078** |
| `fct_fx_rates_periods` | 527 |

Removing it from the seed deletes 10,078 rows from one of three FX tables and
nothing from the other two. That is the hardest shape to notice for a reason
worth stating: **a total outage is obvious and a total agreement is verifiable,
but a disagreement between two tables that both look plausible has no
signature.** Someone charting from `published` and someone charting from `daily`
each get a self-consistent answer, and the two only meet in a meeting.
</details>

---

## 🔍 Investigate 1 — how much of the warehouse do 425 tests actually look at?

> Real warehouse (`data/warehouse.duckdb`), not the sandbox.

`analytics.pipeline_tests` has one row per test, with the model and column it
guards. `information_schema.columns` has every column in the warehouse. Join
them and you can measure something the dbt docs page cannot show you: not how
many tests there are, but how much of the data is under one.

**Questions.**

1. How many columns does `marts` have, and how many carry at least one test?
2. For each `accepted_range` on `marts.fct_emissions_energy`, how many of the
   43,138 rows does it actually examine? Rank them. Which column is the *only*
   one examined on every row, and what is special about it?
3. `dbt_test__audit` holds one table per test. Count them, and compare against
   the number of tests in `analytics.pipeline_tests`. Explain the difference.
4. Given (1) and (2): is this test suite under-built? Argue both sides before
   you look. The answer is not a number.

```bash
just pipeline-status   # only if analytics.pipeline_tests is stale
```

<details>
<summary>Reveal</summary>

**1. 367 columns in `marts`, of which 162 carry a test**: 44%.

```sql
with mart_cols as (
  select table_name as model, column_name
  from information_schema.columns where table_schema = 'marts'
),
tested as (
  select distinct tested_model, tested_column from analytics.pipeline_tests
  where tested_column is not null
)
select count(*) as mart_columns,
       count(*) filter (t.tested_column is not null) as covered
from mart_cols m
left join tested t on t.tested_model = m.model and t.tested_column = m.column_name;
```

Set that beside the *contract* coverage: all 17 marts are contract-enforced, so
**every one of those columns has its type pinned** and 44% have their values
checked. Those are two different guarantees and it is worth being able to say
which one you have.

**2.** Every range test on the mart except one examines a minority of the table:

| column | rows examined | share |
|---|---|---|
| `electricity_price_eur_kwh` | 701 | **1.6%** |
| `consumption_co2` | 4,064 | 9.4% |
| `renewables_share_pct` | 4,459 | 10.3% |
| `carbon_intensity_elec_g_kwh` | 5,561 | 12.9% |
| `low_carbon_share_elec_pct` | 6,421 | 14.9% |
| `life_expectancy` | 14,071 | 32.6% |
| `population` | 14,292 | 33.1% |
| `co2_mt` | 23,370 | 54.2% |
| `year` | **43,138** | **100%** |

`year` is examined on every row because it is the only one of the fourteen that
is never null: it is half the grain. Everything else is a column on a fact built
off the country-year spine (module 01), where a null means "this source does not
cover this country-year", and a null is invisible to `accepted_range`.

The €1/kWh ceiling on the Eurostat price — a genuinely useful test, since that
column is the one place a cents/euros mix-up could enter — is being asked about
1.6% of the fact. That is not an argument against the test. It is an argument
for knowing the number before you quote "43,138 rows, fully range-checked" to
anybody.

**3. 391 audit tables against 425 tests: 22 orphans.** dbt writes the audit
schema on every build and never removes a table whose test has gone, and the
alias hash is computed over the test's arguments, so renaming a model orphans
every audit table attached to it. Being empty, an orphan scores as *passing*, so
counting audit tables inflates the suite with tests that no longer exist and
cannot fail. `transform/pipeline_status.py` filters against the manifest, keyed
on the manifest being present rather than on the match, so a missing manifest
degrades to bare names instead of emptying the table.

**4. Both sides, honestly.**

*Under-built:* 44% column coverage and a median range test seeing a third of the
rows means most of this warehouse is unasserted. The columns with the *worst*
coverage are the sparse ones, which is backwards: sparse columns are where a
join went wrong.

*Correctly built:* tests are not free, and their cost is not runtime: it is that
every test which can fail on reality trains people to ignore failures. 425 tests
that have never had a false positive are worth more than 900 with a standing
amber. The uncovered columns are largely OWID pass-throughs whose values this
project does not compute; a bound on them tests the publisher, not the pipeline.

The synthesis, and the actual answer: **coverage is the wrong metric.** The right
question is per column, "what is the wrong value I could plausibly get here, and
would anything notice?" That produces very few tests on pass-through columns,
several on anything this repo *derives* (a ratio, a conversion, a gap-fill), and
one on every join that could drop rows, which is the assertion the 369 are
thinnest on and which no `accepted_range` can ever be.
</details>

---

## 💬 Design defence

**(a)** `store_failures` is on for the whole project. It costs 389 tables in the
warehouse, they ship inside the published DuckDB file, and 21 of them are stale.
Defend the setting, and then say what it cost that has nothing to do with disk.

<details>
<summary>Reveal</summary>

The defence is about *where the failure is observed*. A test failure in CI or in
the Dagster daemon is a log line on a machine you are not sitting at, describing
rows you now have to reproduce locally, which means rebuilding the warehouse
from live sources that may have moved since. `store_failures` turns that into
`select * from dbt_test__audit.<test>`, on the artefact that actually failed.
For a monthly release built by a workflow, that is the difference between
diagnosing a failure and re-enacting it.

The cost that is not disk: **those tables held clear personal data.** 44
`dbt_test__audit` tables carry a `customer_id`, and they are empty only while
the tests pass, so the one release built on a failing retail test would have
shipped identifiers in a schema nobody had thought to classify. The export's
pseudonymisation expands the declared column set by *name* across every schema
for exactly that reason ([`docs/DATA_PROTECTION.md`](../DATA_PROTECTION.md), and
module 08).

The general shape: a debugging aid that persists rejected rows is a debugging aid
that persists *data*, and it inherits every obligation the source data has. Turn
it on, and then go and check what it wrote.
</details>

**(b)** `analytics.pipeline_tests` once reported two failing tests against a
`dbt build` that finished `ERROR=0`. Both were `dbt_utils.equal_rowcount`, both
had passed. Explain the bug, then state the principle it violates.

<details>
<summary>Reveal</summary>

The health page counted rows in each audit table. That is dbt's *default*
verdict, not its definition of one. A test's result is `fail_calc` applied to its
result set, and `equal_rowcount` overrides the default with
`sum(coalesce(diff_count, 0))`: it emits a one-row summary whether it passed or
failed:

| id_a | id_b | count_a | count_b | diff_count |
|---|---|---|---|---|
| 1 | 1 | 265,035 | 265,035 | **0** |

One row, zero difference, passing. `count(*)` reads it as one failure.

The principle: **do not reimplement the verdict of a tool you are reporting on.**
The health page's whole value is agreeing with the build; a second, simpler
definition of "failing" that agrees 367 times out of 369 is worse than no page,
because the two disagreements are exactly where someone will trust the wrong one.
The manifest already carries `fail_calc` and `severity` per node, so reading them
is both correct and less code than the shortcut.

Same family as module 02's cache keys: a derived value that omits part of what it
derives from will be right in the common case and wrong precisely where it
matters.
</details>

**(c)** The grain contract on `dim_grid_emission_factors` is scoped
`where: is_latest_available` and therefore examines 207 of 5,561 rows: under 4%.
Is that a weakened test or a correct one? Give the rule that decides, and apply
it to the CBAM `direct + indirect = total` check, which sees 2,781 of 12,540.

<details>
<summary>Reveal</summary>

**The rule: a `where:` clause is correct when it defines the population the claim
is about, and a weakening when it excludes rows the claim covers but fails on.**
The test to apply is whether you can state the assertion in one sentence that
includes the filter without it sounding like an excuse.

`dim_grid_emission_factors` passes cleanly. The table is one row per country per
*vintage year* — 2025 for 90 countries, 2024 for 105, 2023 for 10, 2022 for 2 —
so "one row per country" is simply false over the whole relation and true over
the latest-available slice. The scoped test is the only way to state the real
grain, and unscoped it would fail on correct data. It is also load-bearing:
`is_latest_available` is a *filter, not a year*, and a
`where year = 2025` cross-section drops more than half the world.

The CBAM check is the harder call, and it is correct for a different reason: the
filter is *not a choice*. `indirect` is published only for cement, fertilisers
and 34 iron-and-steel rows, so on the other 8,129 rows there is no third number
and no equation to check: the test is not declining to look, there is nothing
there. The unscoped version would fail on every row the regulation left blank.

But notice what the two cases do *not* share. The grid-factor test, scoped, still
covers every claim the model makes. The CBAM test, scoped, leaves 8,129 rows with
a direct and a total and **no internal consistency check of any kind**, and that
is a real, permanent gap, not a solved problem. The right response is to write it
down where a reader will find it, which
[`dbt/seeds/_seeds.yml`](../../dbt/seeds/_seeds.yml) does at length, rather than
to let "the annex is checked against itself" pass into the project's folklore.

So: scoping a test is legitimate and often mandatory, and a scoped test always
owes the reader a note saying what is now unchecked. The failure mode is not the
`where:` clause. It is the coverage number nobody ever computed.
</details>

---

## What to carry forward

- A ceiling is a claim that you know the tail. Measure the distribution, set the
  bound at a multiple of the observed maximum so it catches an *order-of-magnitude*
  error, and write the evidence in the description.
- Calibrate against every row the model will ever hold. A fixture slice gets you
  on countries; "current data looks fine" gets you on history.
- A test that fires on reality gets muted, and takes the credibility of every
  other test with it. Refusing to bound a column is a legitimate design decision:
  `trade_co2_share` is −98% to +1023% and correct.
- SQL tests ask "can I prove this false?", so **unknown scores as innocent**.
  Anywhere an expression touches a correlated subquery or a nullable column, work
  out what it returns when the other side is absent.
- `accepted_range` skips nulls. Know what fraction of rows each of your tests
  actually examines; on this mart it ranges from 1.6% to 100%.
- A `where:` clause is correct when it defines the population the claim is about,
  and it always owes the reader a note about what is now unchecked.
- A hand-maintained reference table needs an assertion in **both** directions.
  The one you write first is the one you were already worried about.
- Store your failures, then remember that a table of rejected rows is a table of
  data, with every obligation the source has.

← [02 — Loading twice](./02-loading-twice.md) · [Course index](./README.md) · next: [04 — Denominators, units and coverage](./04-denominators.md)
