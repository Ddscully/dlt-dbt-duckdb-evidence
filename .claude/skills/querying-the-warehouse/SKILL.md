---
name: querying-the-warehouse
description: How to inspect data/warehouse.duckdb in this project — read-only connections, the single-writer lock, real schema names, and checking column names before writing SQL. Use before writing SQL against this warehouse or when a DuckDB connection fails with a lock error.
---

# Querying the warehouse

One DuckDB file, `data/warehouse.duckdb`. Four schemas:

| Schema | Written by | Contents |
|---|---|---|
| `raw` | dlt | `owid_co2`, `owid_energy`, `wb_country`, `wb_wdi`, `eu_elec_prices` |
| `staging` | dbt (views) | `stg_*`, cleaned to `(country_iso3, year)` |
| `marts` | dbt (tables) | `fct_emissions_energy` |
| `analytics` | Polars | `co2_intensity` |

## Always connect read-only for inspection

```bash
uv run python -c "import duckdb; \
  print(duckdb.connect('data/warehouse.duckdb', read_only=True).sql(\
  'select * from marts.fct_emissions_energy limit 5').df())"
```

**DuckDB allows one writer at a time.** A read-write connection left open — a
stray Python REPL, Harlequin, a Dagster run — makes the next `just run` fail with
a lock error. `read_only=True` costs nothing and avoids the whole class of
problem. This is also why the Dagster graph uses `in_process_executor` and puts
all five dlt resources in one op: parallel steps would just fight over the lock.

For interactive poking, `just sql` opens Harlequin. Close it before running the
pipeline.

## Schema names have no prefix

`dbt/macros/generate_schema_name.sql` overrides dbt's default
`<target>_<custom>` naming. The mart is `marts.fct_emissions_energy`, **not**
`main_marts.fct_emissions_energy`. If a query fails with "table does not exist"
and you wrote `main_`, that's why.

## Check column names before writing SQL against `raw`

dlt snake_cases and flattens the source payload. The World Bank's `iso2Code`,
`capitalCity` and `incomeLevel.value` land as `iso2_code`, `capital_city` and
`income_level__value`. Don't infer names from the API docs — read them:

```bash
uv run python -c "import duckdb; \
  print(duckdb.connect('data/warehouse.duckdb', read_only=True).sql(\
  \"select table_name, column_name, data_type from information_schema.columns \
    where table_schema='raw' order by table_name, ordinal_position\").df().to_string())"
```

Staging and marts columns follow [`docs/STYLE_GUIDE.md`](../../../docs/STYLE_GUIDE.md)
and are stable; `raw` columns are whatever dlt inferred.

## Grain and coverage

Everything is `(country_iso3, year)`. Three coverage facts that produce confusing
query results if you don't know them:

- `fct_emissions_energy` sits on the `dim_country_year` spine and left-joins each
  source onto it, so a row exists wherever *any* source reports. Whole columns are
  null in the country-years the rest don't cover — `count(*)` is not a count of
  countries reporting the metric you're actually reading. Filter on the column.
- The mart's `max(year)` is whichever source is furthest ahead (currently Eurostat
  and WDI, a year past OWID CO2). For "the latest year of X", use
  `max(year) filter (where X is not null)`.
- `electricity_price_eur_kwh` is EU/EEA only — null for most of the world by
  design, not by bug.

`dim_country_year` is the *complete* set of country-years, so it's the way to ask
what's missing rather than what's there:

```sql
-- country-years the warehouse has no emissions for
select d.country_name, count(*) as missing_years
from marts.dim_country_year as d
left join marts.fct_emissions_energy as f
    on d.country_iso3 = f.country_iso3 and d.year = f.year
where f.co2_mt is null and d.year between 1990 and 2024
group by d.country_name
order by missing_years desc;
```

## If the warehouse is missing or stale

It's gitignored and rebuilt by the pipeline: `just run` (or `just materialize`
for the graph-ordered version). `just dbt-build` alone rebuilds only the modelled
layers from whatever `raw` already holds.
