# 00 — Setup and the sandbox

← [Course index](./README.md) · next: [01 — Grain is the contract](./01-grain.md)

**Objectives.** Build a warehouse you are allowed to break. Understand why the
exercises run against two different ones. Know how to get back to green.

---

## Once per clone

```bash
just setup             # uv sync: runtime + dev + notebook + orchestration
just course-sandbox    # ~50s, offline
```

`course-sandbox` runs the whole pipeline — dlt ingest, `dbt build` (411 nodes),
both Polars transforms, the observability tables and the Parquet archive — from
the fixtures checked into `tests/fixtures/ingest/`, into
`data/course/warehouse.duckdb`. No network, no credentials, no cloud bill. It is
gitignored and disposable: delete `data/course/` and run it again.

You should see it finish on `PASS=402 WARN=0 ERROR=0 SKIP=0`.

### The four recipes

| | |
|---|---|
| `just course-sandbox` | build (or rebuild from scratch) the sandbox |
| `just course-rebuild` | re-run `dbt build` against it — **the drill inner loop**, ~15s |
| `just course-transform` | re-run the Polars layer, which `course-rebuild` does not touch |
| `just course-query 'select 1'` | one read-only query against it |

`course-transform` exists because the two Polars tables (`analytics.co2_intensity`
and `analytics.retail_rfm`) are written *after* dbt by a separate process, so a
drill on a derived metric — module 04's denominator — is not rebuilt by
`course-rebuild` at all. Knowing which recipe owns which table is itself the
lesson: a warehouse is rarely built by one tool.

> ⚠️ **`just dbt-build` is not `just course-rebuild`.** The plain recipe targets
> `data/warehouse.duckdb` — your real warehouse, the one with 43,138 mart rows
> and months of snapshot history in it. A drill that breaks a model and then runs
> the wrong recipe writes the broken model into the warehouse you care about.
> The course recipes set `WAREHOUSE_PATH` for you; nothing else does.

---

## Two warehouses, and the exercises say which

This is the first real lesson, not a housekeeping note.

| | sandbox (`data/course/`) | real (`data/warehouse.duckdb`) |
|---|---|---|
| built by | `just course-sandbox` | `just run` |
| source | checked-in fixtures | live public APIs |
| network | none | yes |
| rebuild | ~50s | minutes |
| safe to break | **yes** | no |
| used by | 🔧 break-and-fix | 🔍 investigate |

The fixtures are **17 countries**, chosen in `scripts/record_fixtures.py` to
cover every World Bank region and income group, both Eurostat geo-code
exceptions (`EL`, `UK`) and Taiwan, which the World Bank omits. Fixtures filter
*rows* and never *columns*, so a renamed upstream field still fails CI.

That slice is why break-and-fix and investigate use different warehouses. Look at
what the sandbox actually contains:

```bash
just course-query "
select 'stg_country' as src, count(distinct country_iso3) as n from staging.stg_country
union all select 'stg_eu_prices', count(distinct country_iso3) from staging.stg_eu_electricity_prices
union all select 'stg_co2',       count(distinct country_iso3) from staging.stg_co2
union all select 'stg_wdi',       count(distinct country_iso3) from staging.stg_wdi
order by n desc"
```

```
stg_country     228
stg_eu_prices    41
stg_co2          17
stg_wdi          16
```

Three different numbers, and none of them is 17. The country dimension is 228
because `wb_country` is one of three fixtures deliberately **not trimmed** — it
*is* the dimension the `country_overrides` seed is diffed against, so trimming it
would destroy the thing it exists to check. Eurostat is 41 because a JSON-stat
grid is a flat array indexed row-major over every dimension: you cannot subset it
without rebuilding the index. WDI is 16, not 17, because one fixture country has
no World Bank series at all.

A threshold those 17 countries pass, the full 200+ will break. `co2_per_capita`
has a floor and no ceiling because small petrostates legitimately reach
780 t/person — a ceiling calibrated on the sandbox would have looked reasonable
and failed in production. Module 03 is about exactly this.

---

## The drill loop

Every 🔧 exercise runs the same four steps:

```bash
# 1. seed the bug — the drill gives you the edit
$EDITOR dbt/models/marts/fct_emissions_energy_v2.sql

# 2. rebuild
just course-rebuild

# 3. observe: green build, wrong number
just course-query 'select count(*) from marts.fct_emissions_energy'

# 4. get back to green
git checkout dbt/models/marts/fct_emissions_energy_v2.sql
just course-rebuild
```

Step 4 is `git checkout`, always. The sandbox holds no state you cannot rebuild —
which is a property of *this* warehouse and not of warehouses generally. Exactly
two tables here cannot be recomputed from their sources, and module 05 is about
what that changes.

### When a rebuild fails on a lock

```
IO Error: Could not set lock on file … Conflicting lock is held
```

DuckDB takes **one writer at a time**. Close the `just sql write` session or
the Python REPL you left connected. `just course-query` opens
`read_only=True` for this reason, and a read-only connection can coexist with
others — it is the writer that is exclusive.

---

## Verify your setup

```bash
just course-query "
select
  (select count(*) from marts.dim_country_year)        as spine_rows,
  (select count(*) from marts.fct_emissions_energy)    as mart_rows,
  (select count(*) from marts.fct_retail_order_line)   as retail_lines"
```

Expected: `62928`, `4096`, `41089`. If the first two look surprising against each
other — 62,928 spine rows and 4,096 mart rows out of the same build — that is
module 01, and it is the most important idea in the repo.

---

← [Course index](./README.md) · next: [01 — Grain is the contract](./01-grain.md)
