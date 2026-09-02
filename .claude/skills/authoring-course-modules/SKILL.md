---
name: authoring-course-modules
description: Writing or editing the course modules in docs/course/ — the sandbox recipes, the rule that every number in a drill is measured, and the structural contract tests/test_course.py enforces. Use when adding or revising a module, or when a just course-* recipe or a course guard fails. Not needed to work through the course.
---

# Authoring course modules

`docs/course/` teaches this warehouse as training material for analytics
engineers, built around the failures that stay green rather than the happy path.
Modules 00-04 are written and set the format; 05-10 are outlined in
`docs/course/README.md`. Three exercise types, marked in the text: break-and-fix
drills, investigate-the-data questions, design defences.

This skill is for *writing* the material. Working through it as a learner needs
only `docs/course/README.md`.

## The sandbox, and why it isn't `just test-pipeline`

**A drill has to leave a mess behind.** `just test-pipeline` builds into
`mktemp` and is right to — CI wants a warehouse nobody can find afterwards. A
drill wants the opposite: break a model, look at the wrong number, fix it,
compare. `just course-sandbox` is the same offline fixture build at a stable
gitignored path (`data/course/`), plus `course-rebuild` (dbt only, the inner
loop) and `course-query` (one read-only query).

**`just dbt-build` is the trap**: it targets the real warehouse, so a drill run
through the wrong recipe writes a deliberately broken model into
`data/warehouse.duckdb`. The course says so in 00 and the recipes set
`WAREHOUSE_PATH` themselves.

**Module 04 needed a fourth course recipe, and the reason is a real seam in the
stack rather than a convenience.** `just course-rebuild` runs `dbt build` and
nothing else, so the two Polars tables (`analytics.co2_intensity`,
`analytics.retail_rfm`) are untouched by it — a drill on a *derived metric*
rebuilds nothing and the learner reads a stale number as a result. The raw form
is `WAREHOUSE_PATH=... uv run python -m transform.co2_intensity`, which is
exactly the shape module 00 warns about: forget the variable and it rewrites
`analytics` in the **real** warehouse. Hence `just course-transform`.

**The 17-country fixture slice is taught, not apologised for.** Break-and-fix
runs on the sandbox; investigate-the-data runs on the real warehouse, because
coverage is usually the point of those questions and 17 countries cannot show
it. The split is the "a threshold 17 countries pass and 200+ break" lesson made
operational.

**The sandbox's own numbers are a lesson.** 228 countries in `stg_country`
(`wb_country` is one of the three untrimmed fixtures), 41 in Eurostat (a
JSON-stat grid cannot be subset), 17 in CO2, 16 in WDI — so the fact reaches 52
countries on a 17-country slice. Three different numbers, none of them 17.

## Authoring rules

**Every number in the material was measured, including the drill's.** Module
01's drill (`left join co2` -> `inner join co2` in
`dbt/models/marts/country_stats/fct_emissions_energy_v2.sql`) was run: the mart goes 4,096 ->
3,487 rows and **52 -> 17 countries**, EU price rows 701 -> 104, and `dbt build`
reports `PASS=402 WARN=0 ERROR=0` either way. Don't quote a drill's numbers
without seeding it — the whole claim of the course is that the verdict doesn't
move.

**A drill whose fix is `git checkout <file>` silently reverts uncommitted work
in that file.** Module 04's denominator drill restores
`transform/co2_intensity.py` that way, and running it while the yen-figure
correction was still unstaged threw the correction away. Harmless for a learner
with a clean tree, and worth knowing when *authoring* a drill against a file you
are also editing: stage first, or verify the drill last.

## What `tests/test_course.py` enforces

**It is what stops the course rotting**, the same argument as
`tests/test_exposures.py`: cited repo paths must exist, cited `just` recipes must
exist, module links must resolve, and the index must link every module on disk. A
course pointing at a renamed file is worse than no course, because the reader
assumes they are the one who is wrong. `data/` is deliberately outside the
citable roots — it is gitignored and built, so a path under it is correct on a
fresh clone where it does not exist.

**A cited path that git ignores is skipped, and that rule had to be
generalised.** `data/` was kept out of `CITABLE_ROOTS` because it is gitignored
and built, so a path under it is correct on a fresh clone where it does not
exist. `reports/` cannot be excluded the same way — `reports/pages/` and
`reports/sources/` are source while `reports/build/`, `reports/node_modules/`
and `reports/.evidence/` are output of `just report`. So the check asks
`git check-ignore` instead of carrying a second prefix list. Until it did, the
guard **passed on a developer's machine and would have failed CI**: those three
directories exist once you have built the site, and `building-evidence-reports`
cites all three. Found by merging the branch into a clean worktree, not by
running the tests in place — which is the only way this class of bug shows up.

**It scans for `just <recipe>` inside code only.** "just" is an English word and
this documentation is full of it ("exactly what the publisher just served"), so
the search is restricted to fenced blocks and inline spans, with `#` comments
stripped from the blocks — comments are prose. `_RECIPE_DEF` also has to allow a
default (`backfill-wdi start end=''`), or that recipe reads as undefined and
every citation of it fails. Both bugs were found by the guard failing on module
02 rather than by review.

**The structural contract is a section-level bijection, not a count.** Every
`##` section whose heading carries an exercise marker must hold at least one
`<details>` reveal, and no reveal may sit outside a marked section — strict both
ways, so an exercise with no answer and an answer with no question each fail.
`count(marker) == count(<details>)` sounds like the same rule and is not:
`01-grain.md` has three marked headings and five reveals, because its
design-defence section asks (a), (b) and (c) and answers each. `00-setup.md` is
exempt from the exercise rules **by name**, not by "has no markers" — the latter
excuses exactly the half-written module the check exists to catch. Verified by
breaking all six rules and reading the messages back.

**The index's promise that "every drill ends with a verification query" is
enforced, and the second half of the rule is what makes it worth having.**
`missing_verification()` requires the `**Verification.**` marker *and* a fenced
block after it — the literal alone passes a drill that carries the words and no
command, which is the promise broken in the way that reads as kept. The fenced
search is bounded at `<details>`: every reveal is full of fenced SQL, so an
unbounded search finds a block whatever the drill itself holds and the check
measures nothing. What it deliberately does *not* require is the marker alone on
its line — `01-grain.md` writes "**Verification.** When you think it is fixed:"
with the block below, which is good prose and a bad thing to forbid.

## The thesis, and the findings that support it

**Absence is the blind spot the whole course is organised around.** Every test in
this project asserts something about rows that are *present* — `not_null`,
`accepted_range`, `unique_combination_of_columns`, `relationships` — and deleting
rows makes all four *more* likely to pass. Fan-out fails loudly; row loss fails
silently. That asymmetry is module 01's punchline and it is true of the real
project, not just the drill.

### Module 02 — ingestion

**Its drills are measured too, and one of them changed the repo's understanding
of an old bug.** The merge-key drill (drop `indicator` from `WDI_PRIMARY_KEY`)
takes `raw.wb_wdi` from 6,336 rows to **576** and 11 indicators to **1**, while
`staging.stg_wdi` stays at **576 rows** — unchanged, because a pivot's output
grain does not depend on how many input rows feed it. Ten of eleven columns go to
100% null at a constant row count, so the obvious sanity check is structurally
blind to it. `dbt build` reports `PASS=402 ERROR=0` either way.

**`.arrow()` no longer reproduces the 1,000,000-row truncation as written.** In
DuckDB 1.5.5 `.arrow(n)` returns a `RecordBatchReader` (the same object as
`to_arrow_reader(n)`) and dlt *drains* it, so `yield con.sql(...).arrow(n)` lands
every row. The drill stages the original mistake explicitly with
`yield next(...)` — a caller taking the first batch and treating it as the table.
The guidance in `ingest/pipeline.py` is still right; what changed is that the
failure now needs writing on purpose rather than falling out of the obvious call.

**A shape test protects the shape, not the quantity — and the boundary decides
whether it fires.** Truncating the retail read to 10,000 rows *does* fail
`stg_retail_has_exactly_one_positive_cancellation_line`. The real 1,000,000-row
truncation would **not** have: `C496350` sits at position ~76,800 of 1,067,371
(7% in, first sheet), and a 1M cut drops only the last 67,371 rows. So the
fixture slice fails a check production passes — the mirror image of the
17-country trap, and the reason `tests/test_ingest.py` counts rows against the
workbook's own count instead.

### Module 03 — tests

**It measured how much of the warehouse the 460 tests actually look at, and the
answer is the module.** `dbt_utils.accepted_range` compiles to
`where not (col >= min)`, and `not (null >= 0)` is *null*, so every range test
silently skips its nulls. On `fct_emissions_energy` that means each of the
fourteen range tests examines between **1.6%** (`electricity_price_eur_kwh`, 701
of 43,138) and **54%** (`co2_mt`) of the fact — except `year`, the one column
that is never null, at 100%. 162 of the 367 `marts` columns carry any test at
all, against every mart model under a type contract: two different guarantees, and
worth being able to say which one you have. The audit schema is measurable too —
**391 tables against 460 tests**, i.e. 22 orphans, which is the stale-audit-table
bullet in `CLAUDE.md` showing up as a number.

**Drill 1 is the calibration trap with a second axis nobody expects.** Adding
`max_value: 50` to `stg_co2.co2_per_capita` builds `PASS=402 WARN=0 ERROR=0` on
the sandbox, whose maximum is **22.22 — the USA in 1973**, a real and satisfying
peak that is 35x too small. On the real warehouse it rejects **124 rows across 6
countries** (Sint Maarten 782.7, Kuwait 364.8, Brunei 245.1, Qatar, Curaçao, UAE
— refinery economies with tiny denominators). The part that changes the lesson:
**117 of those 124 rows are before 2000 and none at all are since 2020**, so a
reviewer who did the right thing and checked the full 213-country *current*
cross-section would have shipped the same ceiling. A bound is calibrated against
every row the model will ever hold, and a warehouse with history has two axes to
be wrong along.

**Drill 2 is why `dim_currency` needs the `is_quoted` test, stated as a general
rule about SQL tests.** Appending a phantom row to `dbt/seeds/currencies.csv`
leaves **all seven of the seed's own tests green** and fails one mart test. The
seed's `retired_on` check does a correlated `max(rate_date)` lookup, which
returns NULL for a code with no rates, so `retired_on > NULL` is null, `not null`
is null, and the row is never selected: **the test asks "can I prove this false",
and unknown scores as innocent.** The reverse direction (`sed -i '/^PLN,/d'`)
fires the `relationships` test with 7,066 results, and neither test can do the
other's job because they scan different relations. Without the `is_quoted` test
the damage would be `fct_fx_rates_daily` losing PLN's **10,078** rows while
`fct_fx_rates_published` (7,066) and `fct_fx_rates_periods` (527) stay correct —
one of three tables wrong, which has no signature.

**A 1000x units error passes every test on `fct_example_scope2_emissions`.**
`scope2_t_co2e` carries `{min_value: 0}` and no ceiling, and
`share_of_group_pct` is a ratio of two numbers that both moved — so using
`emission_factor_g_co2_per_kwh` where `emission_factor_t_co2_per_mwh` belongs
turns 232,456 tCO2e into 232.5 **Mt** (more than Spain's 215.5 Mt in 2023) with
`PASS=402 ERROR=0`. **A scaling error is invisible to one-sided bounds and to
every ratio downstream of it**, which is module 03's ceiling argument arriving as
a live gap rather than a hypothetical.

### Module 04 — derived metrics

**The spot-vs-average drill is the one where the error cancels out of the level
and doubles in the difference.** Converting the Eurostat price at
`period_end_units_per_eur` instead of `avg_units_per_eur` moves the mean *signed*
price by **+0.14%** (the closing rate is above the average about as often as
below) while the mean *absolute* move is 2.77% and the worst row 7.5% — so a
spot-check of levels finds nothing. Measured on the differences instead, **282 of
1,330 half-over-half changes (21.2%) disagree about whether the price went up or
down**, worst pair 42 points apart. France 2022-S2 is the quotable case: +5.4% in
euros, −2.3% in dollars at the average rate, +8.2% at the closing rate. **An
error common to a period is invisible in a cross-section and maximal in a time
series**, which is the opposite of where people look.

**`usd_per_eur_period_avg` being on the row is what catches that drill**, and it
is the argument for shipping a parameter beside its result generally: the
one-line check `electricity_price_usd_kwh <> electricity_price_eur_kwh *
usd_per_eur_period_avg` goes 0 → 1,373 rows, with no access to the model. It is
also shippable as a dbt test — **0 of 1,373 rows fail on float residue**, which
is the boundary against `first_order_gbp <= net_revenue_gbp` (272 rows, and
rejected for it): that pair compares two independent `sum()`s, this one
reproduces a single scalar multiplication of two stored doubles and is bit-exact.
**"Never compare floats for equality" is really "floats are order-dependent under
aggregation".**

### Cross-section traps (modules 03-04)

**An empty cross-section is safe and a plausible one is dangerous, and the
warehouse has one of each.** `where year = (select max(year) …)` on
`fct_emissions_energy` returns 214 rows with `co2_mt` null in every one — a chart
with axes and no bars, which gets investigated within the hour.
`dim_grid_emission_factors` at `year = 2025` returns **90 of 207 rows, fully
populated** — a real cross-section of a real year, missing 117 countries
including Ukraine's 111 TWh grid, and nothing about it looks wrong. That
asymmetry is why the vintage model ships `is_latest_available` as a boolean: the
correct query *cannot* be written as a year literal.

**A coverage floor is calibrated against the column's own ceiling.**
`latest_years.sql`'s `n_consumption >= 100` looks slack beside the others and is
the only honest choice — `consumption_co2` covers 120 countries at its best, so
any floor above that makes the column permanently unavailable. Asking all five
columns for "the latest year with 150 countries" returns
`2024, 2023, 2024, 2025, NULL`, and the NULL is the point: as a `filter` it is an
honest absence, as a `where` clause it would have looked like a broken query.
