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
  'select * from marts.fct_emissions_energy limit 5'))"
```

**DuckDB allows one writer at a time.** A read-write connection left open — a
stray Python REPL, a `just sql write` session, a Dagster run — makes the next
`just run` fail with
a lock error. `read_only=True` costs nothing and avoids the whole class of
problem. This is also why the Dagster graph uses `in_process_executor` and puts
all five dlt resources in one op: parallel steps would just fight over the lock.

For interactive poking, `just sql` opens the DuckDB CLI read-only, which is the
right default — but **it does not let you sit alongside a build, and this skill
said it did until 2026-09-02.** Measured on the pinned DuckDB 1.5.5 against
`data/warehouse.duckdb` itself, driving the recipe rather than a library: `just
sql` exits 1 with `Could not set lock on file … Conflicting lock is held`, and
the Python client agrees. Both directions, across processes:

| Held by another process | A read-only connection | A read-write connection |
|---|---|---|
| read-write | **fails** — this is `just sql` during a build | fails |
| read-only | succeeds | **fails** — this is the next `just run` |

The stand-in for the build was a read-write connection running no DML, which
takes the identical lock; the file was byte-identical before and after, so the
measurement costs nothing to repeat.

So the rule is **one writer XOR many readers**, and `read_only=True` buys
compatibility with *other readers*, never with a build. Both nuisances follow: a
forgotten `just sql` blocks the next `just run`, not only a `just sql write` one,
and a running build locks out every inspection until it finishes. Close the
session before building, and use
[`lake.lakehouse.read_only_connection()`](../../../lake/lakehouse.py) to query
`raw` mid-build — that opens the catalog, never the warehouse, which is why it
is the one read that genuinely does work alongside one.

The way out is not a connection flag: it is to stop writing to the file anyone
reads. [`docs/RUNNING_AS_A_SERVICE.md`](../../../docs/RUNNING_AS_A_SERVICE.md)
§4 designs that — build into a scratch warehouse and swap it in — for a
deployment that has to serve reads and rebuild on a schedule.

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
  duckdb.connect('data/warehouse.duckdb', read_only=True).sql(\
  \"select table_name, column_name, data_type from information_schema.columns \
    where table_schema='raw' order by table_name, ordinal_position\").show(max_rows=500)"
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

## Querying the landing tables

`raw` is **not in the warehouse file.** dlt lands it in the DuckLake catalog under
`data/lakehouse/`, and the DuckDB file holds only what dbt builds. So a query
against `raw` needs the catalog attached, and this also works while the pipeline
holds the warehouse's writer lock, because it never opens the warehouse:

```bash
uv run python -c "
from lake.lakehouse import read_only_connection
con = read_only_connection()
print(con.execute('select sum(co2) from lakehouse.raw.owid_co2 where year = 2020').fetchone())
"
```

**Do not reach for `read_parquet` over `data/lakehouse/data/`.** It looks like a
hive archive and it is not the table: DuckLake writes positional delete files
that only the catalog applies, so a glob either fails on the schema mismatch or
returns superseded rows alongside current ones. The catalog is the table.

To ask *what changed* rather than what is there, diff two snapshots — dlt rewrites
`_dlt_id`/`_dlt_load_id` on every merged row, so DuckLake's own change feed
reports a routine reload as a full-table revision and `revisions()` projects
those columns away:

```bash
uv run python -c "
from lake.lakehouse import WEATHER_TABLE, revisions, versions
v = versions(WEATHER_TABLE)
print(len(revisions(WEATHER_TABLE, v[-2], v[-1])), 'rows genuinely restated') if len(v) > 1 else print('first load')
"
```

## `history` is not rebuildable

`history.snap_co2_estimates` is a dbt snapshot: SCD2 versions of OWID's CO2
numbers, appended to on every `dbt build`. Every other table in the file can be
recreated from the sources; this one can't. Don't delete the warehouse to fix an
unrelated problem without meaning to throw the revision history away, and don't
hand-edit `raw.owid_co2` in the real warehouse to test something — the snapshot
records the fake version permanently. Use a `WAREHOUSE_PATH` copy for that.

Query it through `marts.fct_co2_estimate_versions` (first vs. current value per
country-year, `is_revised`) rather than the raw SCD2 table, unless you need the
individual validity windows.

## If the warehouse is missing or stale

It's gitignored and rebuilt by the pipeline: `just run` (or `just materialize`
for the graph-ordered version). `just dbt-build` alone rebuilds only the modelled
layers from whatever `raw` already holds.
