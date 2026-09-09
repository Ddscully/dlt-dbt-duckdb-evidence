# Style guide

How SQL and models are written in this repo. Adapted from dbt Labs'
[How we style our dbt projects](https://docs.getdbt.com/best-practices/how-we-style/0-how-we-style-our-dbt-projects),
trimmed to what actually applies to a DuckDB-backed project and reconciled with
the rules `sqlfluff` already enforces.

Two markers below:

- **[lint]** — enforced by [`.sqlfluff`](../.sqlfluff); `just lint` fails on it.
- **[convention]** — not machine-checked. Reviewers (and agents) enforce it.

dbt Labs' point stands: the specific rules matter less than having them written
down and applied consistently.

## SQL formatting

- **[lint]** Four-space indents, spaces not tabs.
- **[lint]** Keywords, function names, identifiers and literals are lowercase.
- **[lint]** Trailing commas — `a,` at the end of a line, never `, a` leading.
- **[lint]** `as` is explicit when aliasing a column or a table: `co2 as co2_mt`,
  `from co2 as c`. Never the bare `co2 co2_mt` form.
- **[lint]** Lines wrap at 120 characters.
- **[convention]** `union all` over `union` unless you mean to dedupe.
- **[convention]** Write the join type out: `left join`, `inner join` — never a
  bare `join`.
- **[convention]** Joins move left to right. A `right join` is a signal that the
  `from` table is the wrong one.
- **[convention]** Use `--` for comments that should survive into compiled SQL
  and `{# #}` for notes meant only for the reader of the model file.
- **[lint]** A `filter` on a windowed aggregate dedents to the function's own
  indent and the `over` that follows it indents one level further —
  `min(x)` / `filter (…)` / `    over (…)`. It looks wrong and it is what
  `layout.indent` wants; `sqlfluff fix --rules layout.indent` settles the
  argument in one pass. That command has to run with an absolute `LAKEHOUSE_DIR`
  like everything else that reaches the dbt templater, so prefer `just lint` to
  find the line and `sqlfluff fix` only to see the layout it has in mind.

### Deliberate deviations from dbt's guide

| dbt Labs says | We do | Why |
|---|---|---|
| Lines wrap at 80 chars | 120 (`max_line_length` in `.sqlfluff`) | The wide mart's column list and the ISO3/year join predicates read worse when folded at 80. |
| Avoid table aliases in join conditions | Short aliases allowed in marts, and in the four staging models that join or union | `fct_emissions_energy` joins five CTEs on the same two keys; `c.year = e.year` is more scannable than the full CTE name repeated ten times. This row used to end "staging models select from a single source and take no alias at all", which was true when it was written and is not now: `stg_country`, `stg_retail_lines`, `stg_weather_daily` and `stg_eu_electricity_prices_semiannual` all take one — see the structure deviations below. |
| `group by 1, 2` (positional) | **[lint]** Name the columns: `group by country_iso3, year` | Both staging models that aggregate already spell them out, and the grain is the whole contract here — writing it in the `group by` makes a regression visible in the diff. |
| Sort/dist keys in-model | N/A | DuckDB has neither. Materialization lives in `dbt_project.yml` per directory. |

## Model structure

- **[convention]** Every `{{ ref() }}` and `{{ source() }}` goes in an import CTE
  at the top of the file, one per line, named after the thing it references
  (`stg_co2` → `co2`). Nothing else refs mid-model.
- **[convention]** Never hardcode a table name. `{{ ref() }}` and `{{ source() }}`
  are what build the Dagster asset graph — a hardcoded `marts.foo` is invisible
  to lineage.
- **[convention]** CTEs do one logical unit of work and are named for what they
  do, not what they contain.
- **[convention]** A CTE duplicated across two models becomes its own model.
- **[convention]** Open the file with a `--` comment stating what the model is
  and its grain. Both existing marts do this; keep it up.

### Deliberate deviations from dbt's structure guide

The table above records only *formatting* departures, which left the structural
ones reading as oversights. dbt Labs'
[Staging: preparing our atomic building blocks](https://docs.getdbt.com/best-practices/how-we-structure/2-staging)
sets five rules for the staging layer, and this project breaks all five on
purpose. Counts are of the nine staging models, measured rather than recalled.

| dbt Labs says | We do | Why |
|---|---|---|
| ❌ Joins in staging | **3 of 9** join — `stg_retail_lines`, `stg_weather_daily`, `stg_eu_electricity_prices_semiannual` | The same page's DRY rule says to push an always-wanted transformation as far upstream as possible, and here the two rules conflict. Each of these resolves a key every consumer would otherwise redo: the `retail_country_map` seed gives four downstream models a conformed `country_iso3`, and the other two read capital coordinates and the ISO2→ISO3 map out of `stg_country`. Those two are also the *only* reason `stg_country` is `protected` rather than `private`, so the departure is already visible in the access rules. |
| ❌ Aggregations in staging | **2 of 9** aggregate — `stg_wdi`, `stg_eu_electricity_prices` | `stg_wdi` pivots 192,390 long rows over 11 indicators into 17,160 country-years; without it the model's grain is `(indicator, country, year)` and every consumer repeats the pivot. `stg_eu_electricity_prices` averages 1,373 semi-annual rows into 701 annual. dbt's stated cost — losing access to source data you will want later — does not apply: the long form is still `lakehouse.raw.wb_wdi`, and the semi-annual grain ships as its own model *and* its own mart. |
| One staging model per source table | **8 of 9** call `source()` exactly once; the ninth reads a staging peer | The rule holds wherever a source is involved. `stg_eu_electricity_prices` is the exception and is a derived convenience — the annual average exists to join prices to the country-year grain, and it carries `n_half_years` so a reader can tell a year from half a year. |
| One source per source system | **One `raw` source, 8 tables**, six publishers | `raw` is one dlt landing schema in one DuckLake catalog, not six systems' schemas. Splitting the declaration would describe a topology the warehouse does not have. |
| `stg_[source]__[entity]s`, plural | `stg_<entity>`, plurality following the noun | The double underscore exists to disambiguate publisher from entity; no two of the six publishers here supply the same entity, so it would add a word without removing an ambiguity. Plurality follows the noun instead of the rule — `stg_retail_lines` and `stg_fx_rates` are plural, `stg_co2` and `stg_energy` are mass nouns. |

Not on that list, because it is a live judgement rather than a settled decision:
**`staging/` is flat**, where the guide wants a subdirectory per source system.
`marts/` was split into four folders because `_groups.yml` already declared four
domains with enforced `access` between them, so the folders matched a boundary
dbt itself checks. Staging has no equivalent axis — a publisher is not a group
here — and nine models in one directory has not yet cost anything. A seventh
publisher is the point to re-ask.

## Naming

The grain of every staging model and every fact is **`(country_iso3, year)`**.
That contract drives most of the naming below.

- **[convention]** `snake_case` everywhere — schemas, tables, columns.
- **[convention]** Model prefixes: `stg_` for staging views, `fct_` for facts,
  `dim_` for dimensions, `snap_` for snapshots. Underscores only, never dots.
- **[convention]** Join keys keep the same name in every model that has them:
  `country_iso3` and `year`, and equally `currency_code` and `date_key` — the
  key a conformed dimension publishes is the key every fact spells. Not `iso3`,
  not `iso_code`, not `country_code`. Renaming to the contract is the staging
  layer's job — `stg_co2` maps OWID's `iso_code` to `country_iso3` on the way
  through, and `stg_fx_rates` maps the landing table's `quote_currency` to
  `currency_code`. **This rule was written before anything checked it, and two
  marts broke it for months**: `fct_fx_rates_published` and
  `fct_fx_rates_periods` said `quote_currency` while `dim_currency` published
  `currency_code` and their own sibling `fct_fx_rates_daily` spelled it the
  conformed way. Every guard here is scoped to one relation, so a key spelled
  two ways is three green models; the bus matrix in `docs/WAREHOUSE.md` is what
  finally saw it, because it is the only thing that reads across relations.
- **[convention]** Qualify **both** sides of a correlated subquery, always —
  `where r.currency_code = currencies.currency_code`, never
  `where r.currency_code = currency_code`. An unqualified name binds to the
  innermost scope, so the moment the inner and outer tables share a column name
  the correlation silently becomes `r.x = r.x`. The rule above makes that
  collision *more* likely, not less: conforming a key is exactly what puts the
  same identifier in two scopes. Measured on the `currencies` seed's
  `retired_on` test, where the rename would have turned a per-currency
  comparison into a panel-wide one.
- **[convention]** Spell things out. `country_iso3`, not `cty`. Readability beats
  brevity; the one exception is join aliases in wide marts (above).
- **[convention]** Units live in the column name: `co2_mt`, `primary_energy_twh`,
  `gdp_per_capita_usd`, `electricity_price_eur_kwh`. A bare `co2` column is a
  future unit bug.
- **[convention]** Percentages are suffixed `_pct` and stored 0–100, not 0–1.
- **[convention]** Booleans are prefixed `is_` or `has_`.
- **[convention]** Dates are `<event>_date`, timestamps are `<event>_at` and UTC.
- **[convention]** Business terms over source terms. The World Bank calls it
  `NY.GDP.PCAP.CD`; we call it `gdp_per_capita_usd`.

### Column ordering

Facts list columns as: **keys → dimensions → measures**, with measures grouped by
source and a `--` comment naming the group. `fct_emissions_energy` is the
reference implementation — `country_iso3`, the `stg_country` attributes, `year`,
then `-- emissions`, `-- energy`, `-- economic / social (World Bank WDI)`.

## Tests and documentation

- **[convention]** Every model gets a `description` in its YAML that says
  something the column name doesn't already say. "The country ISO3 code" on
  `country_iso3` is noise; "ISO3 code; aggregates like *World* and *Europe* are
  dropped upstream in `stg_co2`" is not.
- **[convention]** Document the *grain* on every model description, and any
  column whose coverage is partial. `electricity_price_eur_kwh` being null
  outside the EU/EEA is the kind of thing that has to be written down.
- **[convention]** Test the grain contract, not everything: `not_null` on the
  join keys, and a uniqueness test on `(country_iso3, year)`.
- **[convention]** Prefer a smaller number of high-value tests over exhaustive
  coverage. A failing test nobody acts on is worse than no test.

## Python (ingest / transform / orchestration)

`ruff` (lint + format) runs via pre-commit and owns formatting; the conventions
below are the ones it can't check.

- **[convention]** The layers stay independently runnable. `orchestration/`
  imports `ingest`, `dbt` and `transform` — it never reimplements them. If you
  add logic to a Dagster asset that isn't wiring, it belongs in the layer.
- **[convention]** `orchestration/assets.py` must not use
  `from __future__ import annotations`. See CLAUDE.md for why.
- **[convention]** Column and table names produced by Python match the SQL
  conventions above — `snake_case`, units in the name.

## Where the rules live

| File | Owns |
|---|---|
| [`.sqlfluff`](../.sqlfluff) | The **[lint]** rules above |
| [`.pre-commit-config.yaml`](../.pre-commit-config.yaml) | Runs sqlfluff + ruff on commit |
| [`CLAUDE.md`](../CLAUDE.md) | Stack gotchas and per-source quirks |
| This file | Naming and structure conventions |

Run `just lint` before committing SQL. Pre-commit runs the same check.
