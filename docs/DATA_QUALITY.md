# Data-quality gates, contracts and ownership

`just dbt-build` runs 374 tests alongside the models — 368 data tests and 6 unit
tests. Dagster surfaces the data tests as asset checks on the models they guard.
For the pytest side, see [`tests/README.md`](../tests/README.md).

## The gates

| Gate | What it catches |
|------|-----------------|
| `dbt_utils.unique_combination_of_columns` on `(country_iso3, year)` | The grain contract, on every fact-shaped staging model, the spine and the mart. `fct_emissions_energy` is four left joins off `dim_country_year`, so one duplicated upstream row would fan the mart out silently. |
| `dbt_utils.accepted_range` | Percentages inside 0–100, non-negative money and tonnage, years inside each source's real span (WDI starts in 1960, Eurostat in 2007), EU electricity under €1/kWh. Unit and index-arithmetic bugs land outside these long before anyone notices a wrong chart. |
| `not_null` / `unique` / `accepted_values` | The country dimension: one row per ISO3, a region for every row, income groups from the World Bank's four. |
| `contract: {enforced: true}` on every mart | The *schema* contract, which the grain contract never saw: 327 columns with a declared type, checked at build time. A column changing type or disappearing under the published Parquet files fails the build instead of arriving in someone's download. |
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

Six of those tests are dbt *unit* tests. They run a model against fixed input
rows and compare the entire output, rather than asserting a property of whatever
the warehouse happens to hold — which is what lets them reach two things a data
test structurally cannot.

**A legal answer that is the wrong one.** `dim_date`'s `fiscal_quarter` is bounded
1–4, so a quarter of 5 is caught and a quarter of 2 where 3 was right is not:
January scoring Q2 under a July year start passes every test in the project.
`stg_retail_lines` is the same problem in a different shape — it is three `case`
expressions and two flags, and `accepted_values` proves an answer is in the list,
never that it is the right member of it. Misclassifying `AMAZONFEE` as a product
moves net revenue by £260,764 with all 22 of that model's data tests green;
dropping the `upper()` from `stock_code` sends all 100 voucher lines, which
arrive lowercase, into product with the same 22 green.

**Logic no data reaches.** `fiscal_year_start_month` is a project var and the
warehouse only ever builds `4`, so eleven of the twelve fiscal policies the model
supports are untested by construction; `overrides.vars` is the only way in, and
the tests pin April, January and July. In `stg_retail_lines`, no `A` invoice has
ever carried a product code, so `is_revenue_line`'s `invoice_type <> 'adjustment'`
term has never once been the deciding one — removing it changes nothing in the
warehouse at all.

Fixtures live in `dbt/tests/fixtures/` (dbt's `test-paths`, not the pytest
fixtures). `dim_date` needs CSV files there because it generates its own rows —
one input year expands to a whole calendar year, and `expect` is full-set
equality over all 366. `stg_retail_lines` is 1:1 on its input, so its cases are
inline.

They run inside `dbt build` rather than being excluded from it. dbt Labs
recommends keeping unit tests out of production runs to save warehouse spend;
that argument is about a cloud warehouse, and this is a local DuckDB build where
all six cost about two seconds. `just dbt-unit-test` is the inner loop.

## Who it's for

Around the tests sits the part that says who this is *for*.

Every model belongs to one of four owned groups — `reference`, `country_stats`,
`compliance`, `retail`. The groups are by domain and not by layer, since a
staging/marts split would put every staging model in one group and nothing would
ever cross the boundary. Staging models are `private` to their group and the
marts are `public`, which dbt enforces at parse time. Two staging models override
to `protected` because they are the only places one domain reads another's
cleaning layer, and the reason sits next to each override.

Each dashboard page and the monthly data release are declared as `exposures`, so
`dbt ls --select +exposure:evidence_retail` answers "what breaks if I change
this". A test fails if a page starts reading a model its exposure doesn't name.

`fct_emissions_energy` is versioned. v2 renamed one column to state its unit and
basis (`co2_per_gdp` → `co2_kg_per_gdp_ppp_2011`), and v1 stays live as a
compatibility view until **2026-11-01**, because the people reading the published
Parquet files can't be paged. Nothing in the repo refs that model and the release
ships it, which is what makes it the right one to version: a rename is free
in-repo and breaking outside it.
