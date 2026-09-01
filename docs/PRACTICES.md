# Practices this repo demonstrates

This warehouse is a working pipeline over seven public feeds, and it is also a
demonstration of a set of data-engineering practices. This page is the index to
that second reading: what each practice is, the failure it prevents, and where in
the code it happens.

**The organising idea is that the expensive failures are the quiet ones.** A
pipeline that crashes gets fixed the same morning. What costs a quarter is a
number that is wrong and plausible — a join that drops a third of the countries
with every test still green, a rate carried over a suspended currency, an
aggregate that changes in its last bits between two runs of the same code. Most
of what follows is machinery for making one of those loud.

Every figure below was measured on this repo rather than estimated, and most of
them were produced by deliberately breaking something and counting what noticed.

---

## 1. Model the data honestly

**Hang facts off an explicit spine, not off whichever source is widest.**
`dim_country_year` is the country dimension crossed with every year the data
covers; the fact inner-joins that and then left-joins each source onto it. This
is what makes coverage answerable — left-join a fact onto the spine and a gap
comes back as a row you can count, rather than as an absence you have to guess
at. The spine is ~63k rows against the mart's ~43k, and eleven small territories
reach the mart with World Bank data and no OWID emissions at all.
→ [`dbt/models/marts/country_stats/dim_country_year.sql`](../dbt/models/marts/country_stats/dim_country_year.sql),
[`fct_emissions_energy_v2.sql`](../dbt/models/marts/country_stats/fct_emissions_energy_v2.sql)

**A key two processes share has to be spelled the same way in both.** The
retail source names countries in its own words — `EIRE`, `RSA`, `USA`, `Korea` —
while every other model here keys on ISO3, so the two halves of the warehouse
could not be joined at all. 34 of the 43 labels happen to match the country
dimension's names, which is the trap: joining on name succeeds quietly and drops
the other nine — 19,898 lines and £652,387, of which `EIRE` alone is £615,520
and the retailer's second-largest market. A seed resolves all 43 once, in
staging, and the join is a *left* one for a reason an inner join makes vivid:
it deletes the rows it cannot resolve, and the `relationships` test that exists
to catch them then passes, because the rows were the evidence. Measured by
deleting one label from the seed — 17,866 lines and £615,520 gone, all 90 nodes
green.
→ [`dbt/seeds/retail_country_map.csv`](../dbt/seeds/retail_country_map.csv),
[`stg_retail_lines.sql`](../dbt/models/staging/stg_retail_lines.sql)

**Model the grain the publisher used, and only then aggregate.** Eurostat
publishes electricity prices every half-year. Averaging that to a year is not
free: the mean absolute half-over-half change was 19% across countries in 2022
against 3–4% through the 2010s, and the Netherlands went from €0.034/kWh in
2022-S1 to €0.142 in S2 as that year's energy-tax cuts landed. The annual
average, €0.088, is a price nobody paid. Both grains are modelled, and the
annual one exists to join prices to emissions — not to chart.
→ [`fct_eu_electricity_prices_semiannual.sql`](../dbt/models/marts/country_stats/fct_eu_electricity_prices_semiannual.sql)

**Where two answers are both correct, ship both and name them.** Converting a
balance uses the closing rate; converting revenue uses the period average. Using
one where the other belongs is a standard finance bug and it is invisible —
a plausible number comes out either way. So the warehouse publishes both, and
carries a column measuring the gap: for EUR/USD it reaches +11.7% in 2003, and
across every currency the worst complete year is the Icelandic króna in 2008 at
+98%.
→ [`fct_fx_rates_daily.sql`](../dbt/models/marts/reference/fct_fx_rates_daily.sql),
[`fct_fx_rates_periods.sql`](../dbt/models/marts/reference/fct_fx_rates_periods.sql)

**A figure in a chart should be derived, not typed.** Coverage does not end in
the same year for every metric, so no page hardcodes a "latest year": each reads
the latest year its own metric family can actually populate. Cutting an energy
chart to the latest CO₂ year quietly drops two thirds of its sample — `co2_mt`
holds 214 countries into the latest year where `primary_energy_twh` collapses to
79.
→ [`reports/sources/warehouse/latest_years.sql`](../reports/sources/warehouse/latest_years.sql)

## 2. Make a table promise something

**First, a word this repo uses precisely.** In the BI sense a *mart* is the
subject area a reader works with, and there are **four**:  `country_stats`,
`reference`, `retail` and `compliance`. `marts/` is dbt's name for the presentation
*layer*, one folder per mart, and the 20 relations inside it are **mart models**.
Getting that backwards is easy and this repo did it — it counted models and
called them marts, so a stale figure sat in five files through two additions to
the layer.

The boundary between the four is not a folder convention. `access` is enforced at
**parse** time, so a cross-mart dependency fails before anything builds, and the
folders exist so the four are visible in the tree as well as in the ymls.

All four gates below are declarative, and all four are enforced by something that
fails — the point of the layer is that none of it is a comment.

**Declare the grain as a test.** Every fact-shaped model carries
`unique_combination_of_columns` on its key. It has been holding the grain since
the start.
→ [`dbt/models/marts/country_stats/_country_stats.yml`](../dbt/models/marts/country_stats/_country_stats.yml)

**Enforce a schema contract on everything that leaves.** All 19 mart models are
contract-enforced, across 397 declared columns each carrying a `data_type`.
The grain test and the schema contract catch different things: the contract is
what sees a column change type under a consumer. Verified by declaring `year` as
`VARCHAR` — the build fails with a per-column mismatch table *before writing
anything*.
→ [`dbt/models/marts/`](../dbt/models/marts/) — one folder and one yml per mart,
so the boundary is the one dbt itself can check rather than a filing convention

**Say what a measure means under `sum()`.** A contract states a column's type
and a test states that it is correct; neither says whether adding it up is
meaningful. Half the numeric columns here — 92 of 188 — are ratios, rates,
prices, averages or extrema, where a sum is nonsense that comes back as a number.
Every one carries `meta: {additivity: …}` from a closed four-value vocabulary,
the 13 `semi_additive` ones have to say in prose *which* direction fails
(`population` gives person-years across years; `cumulative_co2` recounts every
earlier year), and the labels ship in the release manifest so a Parquet consumer
who cannot be paged has them too. Guarded three ways: exhaustive over the layer,
closed vocabulary, and no ratio-shaped name may be declared summable — which
holds across the tree with no exceptions.
→ [`tests/test_additivity.py`](../tests/test_additivity.py),
[`_country_stats.yml`](../dbt/models/marts/country_stats/_country_stats.yml)

**A dimension every domain joins to is a published table, not a staging view.**
The country dimension lived in `staging` and five models across three groups
reached into it, which is why it carried an `access` override; the release
published country names only as columns repeated inside facts, so a consumer
wanting the 228 countries had to deduplicate a 62,928-row spine. `dim_country`
is that dimension, contracted and public. The copies stay, and that half is
measured rather than conceded: stripping `country_name`, `region` and
`income_group` from `fct_emissions_energy` saves 6.7 kB of a 1,591 kB Parquet —
0.4%, because zstd dictionary-encodes 228 repeated strings to nearly nothing —
and no copy can drift, since every one is built from the dimension in the same
run. Purity that costs eight dashboard pages a join and buys 0.4% is not a
practice.
→ [`dbt/models/marts/reference/dim_country.sql`](../dbt/models/marts/reference/dim_country.sql)

**Say who owns a model and who may depend on it.** Four groups by *domain*, not
by layer — a staging/marts split would put every staging model in one group and
nothing would ever cross it. Enforcement is real and was verified by breaking it:
flipping one model to `private` fails `dbt parse` naming its consumer, not
`dbt build` an hour later.
→ [`dbt/models/_groups.yml`](../dbt/models/_groups.yml)

**Declare who is reading, per page rather than per site.** Ten exposures, so
`dbt ls --select +exposure:evidence_retail` answers "what breaks if I change
this" for one dashboard page.
→ [`dbt/models/_exposures.yml`](../dbt/models/_exposures.yml)

**Version a model instead of renaming a column under its consumers.** The one
versioned model renames a column whose old name gave neither unit nor basis. v1
is a *view* over v2 with the one column put back — not a second copy of the
logic or of the 43k rows — and it carries a deprecation date that also appears in
the release notes, because the consumers who need it never read a dbt log.
→ [`fct_emissions_energy_v1.sql`](../dbt/models/marts/country_stats/fct_emissions_energy_v1.sql)

## 3. Write tests that can see a wrong answer

**A data test cannot see an answer that is wrong but legal.** `dim_date`'s fiscal
quarter is range-checked 1–4, which caught a float-division bug at quarter 5.
Change the same expression from `/ 3` to `/ 4` and every fiscal quarter in the
warehouse is wrong while **all 19 data tests on the model pass** — measured, not
argued. Three unit tests fail on it. There are 30 unit tests over twelve models,
and each exists because of a specific mutation the data tests could not see.
→ [`dbt/models/marts/_unit_tests.yml`](../dbt/models/marts/_unit_tests.yml)

**A test earns its place by mutation, and "nothing went red" is the finding.**
The method: break the model in a plausible way against a *copy* of the warehouse,
run its full suite, and record the number that moves. Across seven models, 38
mutations were run and the data tests caught 5.
→ [`.claude/skills/unit-testing-dbt-models/SKILL.md`](../.claude/skills/unit-testing-dbt-models/SKILL.md)

**Determinism is a property to pin — and a determinism guard has to be mutated
repeatedly, not once.** The tie-break in the returns-matching model is what makes
it reproducible between builds. Its unit test passed with that tie-break deleted:
DuckDB's parallel `asof join` draws a different tied row each run, so 300 runs of
the broken model returned all three candidates, and the test scored it green
**28.7% of the time**. Four independent tie groups take the false pass to 1.1%.
Two spot-checks had called it broken-but-stable; they were unlucky draws.
→ [`int_retail_return_matches.sql`](../dbt/models/intermediate/int_retail_return_matches.sql)

**Aggregating floats is not reproducible, and the fix is to stop quoting counts.**
Two `dbt run` invocations against byte-identical sources gave 5,781 and 5,785
distinct values of a summed revenue column: floating-point addition is not
associative and DuckDB's parallel aggregation fixes no order. Every disclosure
figure in this repo is therefore a *share*, which is stable to a tenth of a
point, never a count.

## 4. Guard the failures that stay green

**Every hand-maintained list is asserted against the authority it copies.** Not
one of these failures is loud on its own: an unlisted source yields no row and
the pipeline page under-reports while looking complete; an unregistered asset
check simply never runs; a stale count reads as authoritative.
→ [`tests/test_report.py`](../tests/test_report.py) (dashboard queries against
the assets that write them), [`tests/test_ingest.py`](../tests/test_ingest.py)
(indicator codes against the SQL that pivots them),
[`tests/test_export.py`](../tests/test_export.py) (release attribution against
the sources and the README),
[`tests/test_workflows.py`](../tests/test_workflows.py) (workflow trigger paths
and the shared environment action)

**Registration by hand needs a test, because an omission is not an error.**
Dagster takes explicit lists, so a forgotten asset is simply not in the graph and
`dagster definitions validate` passes. Three assets and two checks sat
unregistered until a downstream job failed in CI naming the symptom and not the
cause; the checks failed more quietly still, by never running.
→ [`orchestration/definitions.py`](../orchestration/definitions.py),
[`tests/test_definitions.py`](../tests/test_definitions.py)

**Numbers written into prose are untested assertions, so they are tested.** One
test count moved from 368 to 369 in two files and nowhere else, leaving fourteen
sites stale with `dbt build`, `pytest` and the linter all green throughout.
→ [`tests/test_documented_counts.py`](../tests/test_documented_counts.py)

**A scanner that stops matching reports nothing, which is indistinguishable from
a clean tree.** Several guards here carry a second test asserting the scan still
finds something — the cheapest way to stop a guard going quiet.
→ [`tests/test_course.py`](../tests/test_course.py)

## 5. One graph across four tools

**Asset keys are the join between the layers, and a mismatch is silent.** dlt
resources are keyed to match the keys dagster-dbt derives from dbt's own
`_sources.yml`. Rename a dbt source without renaming the dlt resource and the
graph splits in two — both halves still run, just unconnected.
→ [`orchestration/assets.py`](../orchestration/assets.py)

**Partition only where a partition is a real unit of work.** One source earns
yearly partitions: its API takes a date range, its disposition is `merge`, and
the year is in the primary key. A second source is incremental *and* takes a date
range and is deliberately **not** partitioned — its whole 27-year series is one
three-second request. Merging is not what earns a partition.

**CI runs offline against recorded fixtures; a nightly run against the live
endpoints is what tells you reality moved.** A red pull request therefore means
*this repo* broke, not that a publisher was rate-limiting — and the nightly
opening an issue is the cue to re-record.
→ [`tests/fixtures/ingest/`](../tests/fixtures/ingest/),
[`.github/workflows/nightly.yml`](../.github/workflows/nightly.yml)

**Define the environment once.** Four workflows each set their own paths until a
storage change meant all four needed the same new line and none of them got it.
The failure landed one layer downstream of the layer that chose the wrong value,
and no local recipe could reproduce it — every recipe exported the variable that
hid it.
→ [`.github/actions/setup`](../.github/actions/setup)

**Build observability out of what the tools already emit.** No new
instrumentation: the loader already stamps a load id, dbt already stores failing
rows, and `information_schema` already knows every table's shape. A test's verdict
is read as dbt computes it — `count(*)` over the failures table is only dbt's
*default*, and taking it literally scored two passing tests as failing against a
build that finished clean.
→ [`transform/pipeline_status.py`](../transform/pipeline_status.py)

## 6. The boundary outward

**Publish the warehouse, not only the dashboard.** A monthly release ships the
DuckDB file, the landing zone beside it, a Parquet per modelled table, checksums
and a manifest — so the joined data is usable without running any of this.
→ [`publish/export_warehouse.py`](../publish/export_warehouse.py),
[`docs/PUBLISHED_DATA.md`](./PUBLISHED_DATA.md)

**A published artifact needs a compatibility ceiling, and the tripwire has to
watch the toolchain rather than the artifact.** The manifest carries both who
wrote the file and what format it is in, because only the second answers "can I
open it". Every check that reads the artifact would pass a format bump — they
read a file the same binary just wrote — so the test that actually fires on the
dependency PR is the one that checks what the installed library *writes*.

**Carry forward what no rebuild can reproduce.** Two tables qualify for different
reasons with the same consequence: snapshot history is state in principle, and
the weather archive is unreproducible within the upstream daily budget. One
mechanism serves both, and one count feeds the three places that check it.
→ [`publish/restore_history.py`](../publish/restore_history.py)

**Classify personal data in metadata, apply the policy at the copy, and measure
the result rather than asserting it.** One column identifies a person. Deleting
it does not anonymise the extract, and the number is the argument: 98.6% of the
5,881 customers are unique on three money columns with no id at all. The policy
is applied to the published copy rather than in a model — because the landing
tables ship inside the same file, and because the staging *views* would otherwise
recompute and re-hash an already-hashed value. 51 relations carry that column and
six were declared by hand, so the policy expands by column name across every
schema and then verifies what it rewrote.
→ [`tests/test_privacy.py`](../tests/test_privacy.py),
[`scripts/measure_disclosure_risk.py`](../scripts/measure_disclosure_risk.py),
[`docs/DATA_PROTECTION.md`](./DATA_PROTECTION.md)

**Licence compatibility is a modelling constraint, not paperwork.** The CBAM
regulation's own country electricity emission factors are IEA data under
CC BY-NC-SA 4.0. Ingesting them would put a non-commercial and share-alike
restriction on a data release that is otherwise entirely permissive, so the
warehouse derives its own factor table instead — and the dashboard page says
plainly that the two are not the same measurement. Separately: the weather
source's *data* licence and its *API terms* are different documents, and only
the API terms bind this pipeline. That is why that source is paced and carried
forward rather than refetched, and why the restriction does not follow anyone who
downloads the result.
→ [README's licence section](../README.md#license)

---

## Where the reasoning lives

This page is the index. The arguments, and what each one cost to learn, are in
[`CLAUDE.md`](../CLAUDE.md) and the skills under `.claude/skills/` — written at
the point they were learned rather than reconstructed afterwards.
[`docs/FOR_REVIEWERS.md`](./FOR_REVIEWERS.md) answers the evaluation questions
(SLA, run cost, what breaks at 1000×, what I would do differently), and
[`docs/REUSING_THIS_STACK.md`](./REUSING_THIS_STACK.md) covers what carries over
to a different dataset.
