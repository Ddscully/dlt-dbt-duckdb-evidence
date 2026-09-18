---
name: querying-the-warehouse
description: How to inspect data/warehouse.duckdb in this project — read-only connections, the single-writer lock, real schema names, and checking column names before writing SQL. Use before writing SQL against this warehouse or when a DuckDB connection fails with a lock error.
---

# Querying the warehouse

Two files, and which one holds a schema decides how you reach it:

- **`raw` is in the DuckLake catalog under `data/lakehouse/`, not in
  `data/warehouse.duckdb`.** dlt lands there; see "Querying the landing tables".
- **Everything dbt and Polars build is in `data/warehouse.duckdb`**: `staging` and
  `intermediate` as views, `marts`, `history` and `analytics` as tables. The
  `staging` views read the catalog by name, so they need it attached as
  `lakehouse`, which `just sql` does.

What each schema holds is [`docs/WAREHOUSE.md`](../../../docs/WAREHOUSE.md)
§Schemas — not repeated here, because a table list in a skill is a copy that
goes stale without failing anything.

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
the four `replace` dlt resources in one op: parallel steps would just fight
over the lock.

For interactive poking, `just sql` opens the DuckDB CLI read-only, which is the
right default — but **it does not let you sit alongside a build.** Measured on the pinned DuckDB 1.5.5 against
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

**The second half of that rule is not "one writer *plus* many readers", and
DuckDB refuses to mix the two modes even inside a single process.** Measured
2026-09-10 on the same DuckDB 1.5.5. An instance is cached per file and
`read_only` is part of its *configuration*, so a second `connect()` in one
process asking for the other mode fails in both orders:

```
Connection Error: Can't open a connection to same database file
with a different configuration than existing connections
```

A second read-only connection with the *same* configuration opens fine, sharing
the cached instance. And a cursor inherits its parent's mode: `con.cursor()` on
a read-write connection can `insert`, while one on a read-only connection
raises `Cannot execute statement of type "INSERT"`. So **read-only is a
property of the instance, not a privilege on a connection** — there is no way
to hand a caller a restricted handle onto a writable warehouse, which is why
every reader here opens its own read-only connection rather than being passed
one.

What a single process *does* get is one instance in one mode with many
connections on it. MVCC gives a reader a consistent snapshot while a write is
in flight, and the reader/writer split there is per-*transaction* rather than
per-connection: measured on a scratch file, a reader mid-transaction still sees
the pre-insert count, and 200 interleaved inserts and reads on one instance
complete without contention. That is the arrangement people picture when they
ask for a writer alongside readers, and it is a *server* — one long-lived
read-write instance behind a connection pool. Across processes no configuration
produces it.

**`read_only_connection()` works because of *which file* is locked, not because
DuckLake is more permissive.** The catalog is an ordinary DuckDB file under the
identical rule; a `dbt build` simply is not writing it, because dlt has already
finished. Measured 2026-09-10 with a read-write stand-in holding
`data/lakehouse/catalog.duckdb` and no DML — the file was byte-identical
afterwards — the call is refused, so it fails during `just ingest` exactly as a
warehouse read fails during a build. Worth recognising because it does not read
as a lock error until the second line:

```
IO Error: Failed to attach DuckLake MetaData "__ducklake_metadata_lakehouse"
at path + "duckdb:…/catalog.duckdb"Could not set lock on file
".../catalog.duckdb": Conflicting lock is held in ... (PID 618977) by user dman.
```

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
`income_level__value`. Don't infer names from the API docs — read them, from the
catalog:

```bash
uv run python -c "
from lake.lakehouse import read_only_connection
read_only_connection().sql(\"select table_name, column_name, data_type from information_schema.columns \
  where table_schema='raw' order by table_name, ordinal_position\").show(max_rows=500)"
```

**The same query against `data/warehouse.duckdb` returns zero rows and no error**
(measured 2026-09-15: 0 there, 296 columns in the catalog), which reads as an
answer. This skill and `adding-a-data-source` both gave that query for weeks after
the landing zone moved.

Staging and marts columns follow [`docs/STYLE_GUIDE.md`](../../../docs/STYLE_GUIDE.md)
and are stable; `raw` columns are whatever dlt inferred.

## Grain and coverage

Country facts are `(country_iso3, year)`, and most of the warehouse is country
facts — but not all of it: the FX tables are `(rate_date, currency_code)`, the
retail models sit below country grain, weather lands daily, Eurostat prices are
semi-annual and `fct_cbam_exposure` has no year. `country-stats-models` has the
exceptions. Three coverage facts about the country-year fact that produce
confusing query results if you don't know them:

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

## Connecting a GUI (DBeaver)

Any JDBC client meets the lock and the catalog attach, without `just sql` to
handle either. What follows was measured with DBeaver and its DuckDB JDBC driver
1.5.5.1 on 2026-09-09; the `ATTACH` rules were re-measured on the pinned DuckDB
1.5.5 through the Python client on 2026-09-15.

**Why it fails out of the box.** The `staging` views store SQL that names the
catalog literally (`select * from lakehouse.raw.owid_co2`), and DuckDB resolves
that name at *query* time. A fresh GUI connection opens every table and fails
11 views — the nine `staging` ones and the two `intermediate` ones that read them —
with `Catalog "lakehouse" does not exist!`. Attaching the catalog under any other
alias (`lake`, `ducklake`) fails them identically.

1. **Driver properties → `duckdb.read_only = true`, before the first connect.**
   Without it the GUI takes the writer lock and every `just run`,
   `just dbt-build` and `just materialize` fails until it disconnects.
2. **Connection → Initialization → Bootstrap queries: one entry**, with
   `<LAKEHOUSE_DIR>` being the absolute path `just where` prints:

   ```sql
   ATTACH 'ducklake:duckdb:<LAKEHOUSE_DIR>/catalog.duckdb' AS lakehouse (DATA_PATH '<LAKEHOUSE_DIR>/data/', READ_ONLY)
   ```

   Bootstrap queries run on every physical connection, which is what is wanted:
   DBeaver opens separate ones for the navigator and each editor, and an attach in
   one is invisible to the others.

   With `LAKEHOUSE_DATA_PATH` set, `DATA_PATH` is that URL, and a `CREATE SECRET`
   has to come first as its own entry. With `LAKEHOUSE_CATALOG` set the attach is
   `'ducklake:postgres:<the URL>'` with a `METADATA_SCHEMA` option, needs
   `INSTALL postgres; LOAD postgres` before it, and takes its password from
   `PGPASSWORD` in the client's environment rather than from the connection
   string — the `sql` recipe in the `justfile` spells out all three.
   (Not tried in a GUI.)

Two traps, both of which look like something else:

- **One bootstrap entry is one JDBC statement.** A `LOAD ducklake` and the
  `ATTACH` in the same entry arrive as one statement and fail with
  `Parser Error: syntax error at or near "ATTACH"`; the poisoned connection then
  reports `Attempting to execute an unsuccessful or closed pending query result`
  followed by `Catalog "lakehouse" does not exist!` — cause and effect, not two
  problems. The `LOAD` is not needed at all: the `ducklake:` prefix autoloads the
  extension.
- **`DATA_PATH` must match the path the catalog stored, as a string.** A relative
  spelling, a symlinked route to the same directory and a doubled slash are all
  refused with `DATA_PATH parameter "…" does not match existing data path in the
  catalog`; a missing trailing slash is tolerated. This is the justfile's
  `LAKEHOUSE_DIR` rule met from the GUI side.

Verify with `select database_name, type from duckdb_databases()` — `lakehouse`
must be listed as `ducklake` — and then a view:
`select co2_mt from staging.stg_co2 where country_iso3 = 'DEU' and year = 2020`.

**Don't read `lakehouse.raw_staging`.** It is dlt's merge scratch, a full copy of
each merge table's latest load, not a layer of the warehouse. And `read_only`
makes the GUI a good citizen among *readers* only — disconnect before any recipe
that writes.

## `history` is not rebuildable

`history.snap_co2_estimates` and `history.snap_grid_emission_factors` are dbt
snapshots: SCD2 versions of OWID's CO2 numbers and of the Scope 2 factors,
appended to on every `dbt build`. With `analytics.pipeline_runs` (each dbt
invocation's timings, which `run_results.json` keeps only for the latest) they
are the tables no rebuild can reproduce, and the weather archive in the catalog
cannot be refetched within Open-Meteo's budget. Don't delete the warehouse or the
landing zone to fix an unrelated problem without meaning to throw that away, and
don't hand-edit `lakehouse.raw.owid_co2` to test something — the next build's
snapshot records the fake version permanently. Test against copies, with both
`WAREHOUSE_PATH` and `LAKEHOUSE_DIR` pointed at them (`country-stats-models` has
the recipe).

Query it through `marts.fct_co2_estimate_versions` (first vs. current value per
country-year, `is_revised`) rather than the raw SCD2 table, unless you need the
individual validity windows.

## If the warehouse is missing or stale

It's gitignored and rebuilt by the pipeline: `just run` (or `just materialize`
for the graph-ordered version). `just dbt-build` alone rebuilds only the modelled
layers from whatever `raw` already holds.
