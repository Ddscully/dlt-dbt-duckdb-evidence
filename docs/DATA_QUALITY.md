# Data-quality gates, contracts and ownership

`just dbt-build` runs 364 dbt tests alongside the models, and Dagster surfaces
each one as an asset check on the model it guards. For the pytest side, see
[`tests/README.md`](../tests/README.md).

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
