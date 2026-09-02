# Data-quality gates, contracts and ownership

`just dbt-build` runs 490 tests alongside the models — 460 data tests and 30 unit
tests. Dagster surfaces the data tests as asset checks on the models they guard.
For the pytest side, see [`tests/README.md`](../tests/README.md).

## The gates

| Gate | What it catches |
|------|-----------------|
| `dbt_utils.unique_combination_of_columns` on `(country_iso3, year)` | The grain contract, on every fact-shaped staging model, the spine and the mart. `fct_emissions_energy` is four left joins off `dim_country_year`, so one duplicated upstream row would fan the mart out silently. |
| `dbt_utils.accepted_range` | Percentages inside 0–100, non-negative money and tonnage, years inside each source's real span (WDI starts in 1960, Eurostat in 2007), EU electricity under €1/kWh. Unit and index-arithmetic bugs land outside these long before anyone notices a wrong chart. |
| `not_null` / `unique` / `accepted_values` | The country dimension: one row per ISO3, a region for every row, income groups from the World Bank's four. |
| `contract: {enforced: true}` on every mart model | The *schema* contract, which the grain contract never saw: 397 columns with a declared type, checked at build time. A column changing type or disappearing under the published Parquet files fails the build instead of arriving in someone's download. |
| `dbt source freshness` (`just dbt-freshness`) | Whether the warehouse is stale. dlt stamps every row with `_dlt_load_id`, a unix epoch, so this measures when the *pipeline* last ran (warn at 7 days, error at 30) and not when the publishers last updated. |

Every test runs with `store_failures`, into a `dbt_test__audit` schema. A red
check hands you `select * from dbt_test__audit.<test_name>` and the offending
rows, not a count.

The tests are calibrated to fail on a bug and not on reality. `income_group` is
left nullable because the `country_overrides` territories genuinely have no World
Bank classification, and `co2_per_capita` has a floor but no ceiling because
small petrostates legitimately reach 780 t/person. Before tightening a bound,
check the actual distribution: CI builds a 17-country fixture slice, which will
happily pass a threshold the full 200+ would break.

## Unit tests

Thirty of those tests are dbt *unit* tests, over twelve models — `dim_date`,
`stg_retail_lines`, `stg_weather_daily`, `fct_cbam_exposure`,
`fct_country_weather_year`, `fct_fx_rates_daily`, `fct_fx_rates_periods`,
`fct_retail_returns`, `fct_retail_customer_cohorts` and
`dim_retail_customer`. They run a model against fixed input rows and compare the
entire output, rather than asserting a property of whatever the warehouse happens
to hold — which is what lets them reach two things a data test structurally
cannot.

**A legal answer that is the wrong one.** `dim_date`'s `fiscal_quarter` is bounded
1–4, so a quarter of 5 is caught and a quarter of 2 where 3 was right is not:
January scoring Q2 under a July year start passes every test in the project.
`stg_retail_lines` is the same problem in a different shape — it is two `case`
expressions and two boolean flags built off them, and `accepted_values` proves an answer is in the list,
never that it is the right member of it. Misclassifying `AMAZONFEE` as a product
moves net revenue by £260,764 with all 19 of that model's data tests green;
dropping the `upper()` from `stock_code` sends all 100 voucher lines, which
arrive lowercase, into product with the same 19 green.

`fct_cbam_exposure` is the hardest of them. Its numbers are transcribed from
a legal instrument, so there is nothing independent to check them against and its
20 data tests are almost all `not_null` and generous ranges — 19 of them when
the mutations below were run, the twentieth being the route test that came out
of them. What a unit test reaches
instead is the rules: hardcoding the phase-in mark-up at 10/20/30% moves the
fertiliser average from €105.76 to €115.18 a tonne — fertilisers carry a flat 1%
food-security carve-out — with all 19 green, and measuring
`excess_over_cleanest_source` against the product group instead of the good takes
the total from 18,989 to 30,599 tonnes, also with all 19 green.

**Logic no data reaches.** `fiscal_year_start_month` is a project var and the
warehouse only ever builds `4`, so eleven of the twelve fiscal policies the model
supports are untested by construction; `overrides.vars` is the only way in, and
the tests pin April, January and July. In `stg_retail_lines`, no `A` invoice has
ever carried a product code, so `is_revenue_line`'s `invoice_type <> 'adjustment'`
term has never once been the deciding one — removing it changes nothing in the
warehouse at all. In `fct_cbam_exposure` the fallback rule is the same story: the
regulation sends a listed country with no value for a good to the "other
countries" row *as a whole line*, and resolving it column by column instead
produces a figure that exists nowhere in the regulation — but the row that once
proved it was corrected out of the annex in July 2026, so today the mutation
changes not one number in the warehouse.

Fixtures live in `dbt/tests/fixtures/` (dbt's `test-paths`, not the pytest
fixtures). `dim_date` needs CSV files there because it generates its own rows —
one input year expands to a whole calendar year, and `expect` is full-set
equality over all 366. The other seven are 1:1 on their inputs, or close enough
that posing the rows directly is clearer, so their cases are inline. `fct_cbam_exposure`'s fixtures also pick totals that are float-exact
under the mark-up, because `markup_2026_pct` is a ratio of two doubles and the
warehouse holds three distinct values of it that all print as `10.0`.

They run inside `dbt build` rather than being excluded from it. dbt Labs
recommends keeping unit tests out of production runs to save warehouse spend;
that argument is about a cloud warehouse, and this is a local DuckDB build where
all twenty-eight cost 4.2 seconds. `just dbt-unit-test` is the inner loop.

## Which measures may be summed

A test says a column is *correct*. Nothing in a schema says what a column
*means* under aggregation, and that is where a plausible wrong number comes
from: `sum(renewables_share_pct)` and `avg(co2_per_capita)` across countries are
both meaningless and both come back a number with no error anywhere.

So every numeric column in the marts layer carries `meta: {additivity: …}`
beside its `data_type`, from a closed vocabulary of four:

| Label | Means | Count |
|-------|-------|------:|
| `additive` | Sum it along any dimension of the table. Money, tonnes, counted rows, durations. | 42 |
| `semi_additive` | Summable some ways and not others; **the column's description says which**. | 13 |
| `non_additive` | Never summable — ratios, rates, prices, averages, extrema, distinct counts. | 92 |
| `not_a_measure` | A key, a calendar part, or a parameter carried on the row. | 41 |

Half of them are non-additive, which is the number that makes the exercise worth
doing. The interesting cases are the middle row: `population` adds across
countries and gives person-years across years; `cumulative_co2` is a stock and
counts every earlier year again; `cohort_size` is constant down a cohort's rows;
`original_quantity` belongs to the matched purchase, and 16,398 matched returns
point at 15,312 distinct purchases, so summing it counts 1,086 of them twice.
The label alone cannot say any of that, which is why a `semi_additive` column
with no description is a test failure.

`tests/test_additivity.py` holds it: every numeric mart column is labelled, the
vocabulary is closed, no column named like a ratio (`_pct`, `_per_`, `share`,
`rate`, `intensity`, `median_`, `avg_`, `price`) is declared summable — that
holds across the whole tree with no exceptions — and the labels reach
`manifest.json` in the release, because a label with no consequence is
decoration.

The five `analytics` tables are written by Polars and invisible to dbt, so their
56 labels are declared in `EXTRA_ADDITIVITY` beside `EXTRA_CLASSIFICATIONS` —
the same split personal data already makes, for the same reason. Two tests hold
them to their authorities rather than to a list: `co2_intensity` is
`select * from marts.fct_emissions_energy` plus two derived columns, so every
copied label must equal the mart's, and `retail_rfm`'s coverage is checked
against the frame the transform actually builds. The copies are *stated* rather
than inherited at runtime, which is the more typing and the safer failure:
inheriting means a mart rename silently takes the copy's label with it.
`staging` stays outside on purpose — it is a cleaning copy of a source whose
measures are declared one layer up.

## Who it's for

Around the tests sits the part that says who this is *for*.

Every model belongs to one of four owned groups — `reference`, `country_stats`,
`compliance`, `retail`. The groups are by domain and not by layer, since a
staging/marts split would put every staging model in one group and nothing would
ever cross the boundary. Staging models are `private` to their group and the
marts are `public`, which dbt enforces at parse time. Two staging models override
to `protected` because they are the only places one domain reads another's
cleaning layer, and the reason sits next to each override.

Those four groups are also how the marts declarations are filed:
`dbt/models/marts/` holds `_country_stats.yml`, `_reference.yml`, `_retail.yml`
and `_compliance.yml` rather than one file for the layer. dbt is indifferent to
which yml declares a model, so the boundary had to be chosen — and choosing the
one dbt already enforces means the file a model is declared in and the group it
answers to cannot drift apart.

Each dashboard page and the monthly data release are declared as `exposures`, so
`dbt ls --select +exposure:evidence_retail` answers "what breaks if I change
this". A test fails if a page starts reading a model its exposure doesn't name.

`fct_emissions_energy` is versioned. v2 renamed one column to state its unit and
basis (`co2_per_gdp` → `co2_kg_per_gdp_ppp_2011`), and v1 stays live as a
compatibility view until **2026-11-01**, because the people reading the published
Parquet files can't be paged. Nothing in the repo refs that model and the release
ships it, which is what makes it the right one to version: a rename is free
in-repo and breaking outside it.
