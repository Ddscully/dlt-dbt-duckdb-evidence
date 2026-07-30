# CLAUDE.md

Guidance for Claude Code (and humans) working in this repo.

## What this is

A public demo of a modern, lightweight data-engineering + BI stack. Everything
runs locally with `uv` against a single DuckDB file — no cloud warehouse.

```
dlt (EL) → DuckDB → dbt (staging/marts) → Polars (heavy T) → Evidence (BI)
                       └─▶ Parquet lake (data/lake/, partitioned by year)
                    all orchestrated by Dagster
```

## Commands

Use the `justfile` recipes (they map to plain `uv run …` commands):

| Command | What it does |
|---------|--------------|
| `just setup` | `uv sync --group dev --group notebook --group orchestration` |
| `just ingest` | run the dlt pipeline → `raw` schema in DuckDB |
| `just ingest-wdi-full` | same, ignoring WDI's incremental watermark (full re-fetch) |
| `just dbt-deps` | install dbt packages (`dbt_utils`) into `dbt/dbt_packages/` |
| `just dbt-build` | `dbt deps` then `dbt build` (models, snapshot + 70 tests) |
| `just dbt-freshness` | `dbt source freshness` — is the warehouse stale? |
| `just transform` | Polars derived metrics → `analytics` schema |
| `just pipeline-status` | load times, layer inventory, dbt test state → `analytics.pipeline_*` |
| `just lake` | year-partitioned Parquet archive of the warehouse → `data/lake/` |
| `just run` | ingest → dbt-build → transform → pipeline-status → lake (shell ordering) |
| `just dagster` | Dagster UI on :3000 — asset graph, runs, freshness, checks |
| `just materialize` | same pipeline, ordered by the asset graph |
| `just materialize-select 'raw/wb_wdi*'` | one asset + everything downstream (`*` all, `+` one layer) |
| `just export-data` | package `data/export/` — the DuckDB copy + Parquet + checksums that `release-data.yml` publishes |
| `just test` | `pytest` — mocked-payload unit tests, no network |
| `just test-pipeline` | the whole pipeline against fixtures, into a throwaway warehouse |
| `just record-fixtures` | re-record `tests/fixtures/ingest/` from the live APIs |
| `just lint` | `sqlfluff lint dbt/models dbt/snapshots` |
| `just sql` | open the warehouse in Harlequin |
| `just notebook` | marimo exploration notebook |

Always run tools through `uv run` so they use the project venv. dbt commands
must run from the `dbt/` directory (that's where `profiles.yml` lives).

## Style guide

SQL and model conventions live in [`docs/STYLE_GUIDE.md`](docs/STYLE_GUIDE.md) —
naming, grain, import CTEs, column ordering, and where this project deliberately
departs from [dbt Labs' style guide](https://docs.getdbt.com/best-practices/how-we-style/0-how-we-style-our-dbt-projects).
The formatting half of it is enforced by [`.sqlfluff`](.sqlfluff); run
`just lint` (pre-commit runs the same check).

## Agent skills

Vendor skills for each layer are declared in [`.claude/settings.json`](.claude/settings.json),
so Claude Code offers to install them when you trust this repo. They carry the
tool-level knowledge; this file and the project skills carry the repo-level
knowledge.

| Plugin | Covers |
|--------|--------|
| `dbt@dbt-agent-marketplace` | [dbt Labs' skills](https://github.com/dbt-labs/dbt-agent-skills) — models, tests, docs, debugging |
| `dagster-expert@dagster` | [Dagster's skills](https://github.com/dagster-io/skills) — assets, automation, CLI |
| `polars@polars` | [Polars' skill](https://github.com/polars-inc/skills) — idiomatic lazy-API Polars |
| `duckdb-skills@duckdb-skills` | [DuckDB's skills](https://github.com/duckdb/duckdb-skills) — querying, file formats, docs search |

Not enabled, but worth knowing about: `dbt-migration@dbt-agent-marketplace`
(one-off dbt Core → Fusion work), `dignified-python@dagster`, and dltHub's
[AI Workbench](https://github.com/dlt-hub/dlthub-ai-workbench)
(`/plugin marketplace add dlt-hub/dlthub-ai-workbench`) — the workbench assumes
its own scaffolding, so prefer the `adding-a-data-source` skill below for the
pipeline that already exists here.

Project skills in `.claude/skills/` cover the seams the vendor skills can't know:

- **`adding-a-data-source`** — the cross-layer workflow (dlt resource → dbt
  source → staging → mart → Dagster asset key → Evidence), including the
  name-matching that silently splits the asset graph if you get it wrong.
- **`querying-the-warehouse`** — read-only connections, the single-writer lock,
  clean schema names, checking `raw` column names before writing SQL.
- **`building-evidence-reports`** — the Evidence layer, which has no vendor skill.

## Warehouse schemas (one DuckDB file: `data/warehouse.duckdb`)

- `raw` — dlt landing tables: `owid_co2`, `owid_energy`, `wb_country`, `wb_wdi`,
  `eu_elec_prices`
- `staging` — dbt views, `stg_*`, cleaned to `(country_iso3, year)` grain
- `marts` — dbt tables: `dim_country_year` (the country-year spine),
  `fct_emissions_energy` (the wide join, built on the spine) and
  `fct_co2_estimate_versions` (revision history, off the snapshot)
- `history` — the dbt snapshot `snap_co2_estimates`: SCD2 versions of OWID's CO2
  numbers. **The one table here that no rebuild can reproduce** — see below
- `analytics` — Polars output: `co2_intensity`, plus `pipeline_sources` /
  `pipeline_tables` / `pipeline_tests` (see *Pipeline observability* below)

Grain of every fact/staging model is **`(country_iso3, year)`**; joins are on
ISO3 country code + year. The country dimension (`stg_country`) supplies
`region` and `income_group`.

**The fact hangs off the spine, not off a source.** `dim_country_year` is
`stg_country` × every year the data covers (bounds read from the sources, so both
ends move); `fct_emissions_energy` inner-joins it to the union of country-years
any source reports, then left-joins each source onto that. Consequences worth
knowing:

- A country-year only one source reports still reaches the mart — 11 small
  territories have World Bank data but no OWID emissions, and Eurostat/WDI run a
  year ahead of OWID CO2. Expect nulls in the columns the others don't cover;
  chart queries have to filter for what they need.
- The dimension is authoritative for *what a country is*. Codes it doesn't carry
  can't reach the mart, which is how the World Bank's aggregates (`WLD`, `EUU`)
  and Antarctica stay out.
- `max(year)` on the mart now reports whichever source is furthest ahead, so the
  `mart_covers_recent_years` check measures it per source column instead.
- The spine itself is the full cross join (~63k rows against the mart's ~43k).
  Left-join a fact onto it to see coverage gaps as rows.

## Snapshot history (`dbt/snapshots/`)

`snap_co2_estimates` is an SCD2 snapshot of `stg_co2` (`co2_mt`,
`co2_per_capita`, 1990 onwards, `check` strategy, `hard_deletes='invalidate'`).
OWID restates published years; every other model overwrites the old number, so
this is the only place a revision leaves a trace.
`marts.fct_co2_estimate_versions` summarises it (first vs. current value,
`is_revised`) and `reports/pages/restatements.md` renders it.

- **A snapshot is state, not a build artifact.** `dbt build` appends to it; it
  can't be recomputed from the sources, and deleting `data/warehouse.duckdb`
  destroys the history for good. Every other table here is disposable — this one
  isn't, which is also why it's narrow (two columns, 1990+) rather than the whole
  fact.
- **CI, Pages and the release all start from an empty file**, so in those
  contexts every row is version 1 and `is_revised` is uniformly false. The
  restatements page renders an explicit "nothing revised yet" branch for that
  case; it is the honest state, not a broken build.
- **Verify a snapshot change by simulating a revision**, not by waiting for OWID:
  build, `update raw.owid_co2 set co2 = co2 * 1.05 where iso_code = 'DEU' and
  year = 2019` in a throwaway warehouse, build again, and check
  `fct_co2_estimate_versions`. Don't do it in the real warehouse — the fake
  version stays in the history even after you re-ingest.
- **Evidence can't write a zero-row source to parquet** ("too small to be a
  Parquet file", and the build fails). That's why
  `sources/warehouse/co2_estimate_versions.sql` selects every country-year and
  the page filters on `is_revised` itself, rather than the source pre-filtering
  to the revised ones.

## Data-quality gates (`dbt/models/**/_*.yml`)

`dbt_utils` is the project's only dbt package; it exists for
`unique_combination_of_columns` (the `(country_iso3, year)` grain contract on
every fact-shaped staging model and the mart) and `accepted_range` (percentages
in 0–100, non-negative money/tonnage, per-source year bounds, EU electricity
under €1/kWh). `dbt source freshness` reads dlt's `_dlt_load_id` as a unix epoch.

- **`dbt deps` is not optional any more.** `dbt/dbt_packages/` is gitignored, so
  a fresh clone must run it before `dbt build`, `dbt parse` or `sqlfluff`. The
  justfile recipes depend on `dbt-deps`; the three workflows run it explicitly.
  `dbt_project.prepare_if_dev()` covers it under `dagster dev` only — outside the
  UI, `dbt deps && dbt parse` has to happen before the asset graph will load at
  all, because the manifest lives in the gitignored `dbt/target/`.
- **Test args go under `arguments:`, and the key is `data_tests:`.** The flat
  `tests: [- some_test: {arg: …}]` form is deprecated in dbt 1.10 and gone in
  Fusion; the whole project uses the new spelling, so match it.
- **Every test's failures are stored, not just counted.** `dbt_project.yml` sets
  `data_tests: +store_failures: true` project-wide, into a
  `dbt_test__audit` schema (one table per test, named after it). A red check in
  CI or Dagster gives you `select * from dbt_test__audit.<test_name>` for the
  offending rows instead of just a failure count — verified by breaking
  `co2_mt`'s `accepted_range` in a throwaway warehouse and reading the row back
  out of the audit table.
- **Tests are calibrated to fail on bugs, not on reality.** `income_group` is
  nullable on purpose (the `country_overrides` territories have no World Bank
  classification) and `co2_per_capita` has a floor but no ceiling (small
  petrostates legitimately reach 780 t/person). Before tightening a bound,
  check the actual distribution — the fixture slice is 17 countries and will
  happily pass a threshold the full 200+ would break.
- **Source freshness measures our load, not the publisher's.** `_dlt_load_id` is
  stamped at ingest, so a freshness failure means the pipeline stopped running.
  It is tautologically green in CI (which loads and then checks), which is why
  it is a `just` recipe rather than a workflow step.

## Pipeline observability (`transform/pipeline_status.py`)

`just pipeline-status` writes three flat tables into `analytics` —
`pipeline_sources` (dlt load time, rows and year span per landing table),
`pipeline_tables` (rows and year span per modelled table) and `pipeline_tests`
(every dbt test, what it guards, and how many rows are currently failing it).
`reports/pages/pipeline.md` renders them; the asset is
`analytics/pipeline_status`, downstream of `co2_intensity`.

- **None of it is new instrumentation.** dlt already stamps `_dlt_load_id`, dbt
  already stores failing rows in `dbt_test__audit`, and `information_schema`
  already knows every table's shape. The module exists because two of the three
  need dynamic SQL over a table list that isn't known until runtime, which a
  static Evidence source query can't express.
- **Test names come from `dbt/target/manifest.json`, not the audit table name.**
  dbt truncates and hashes a `store_failures` alias longer than 63 characters
  (`dbt_utils_accepted_range_fct_c_1c6718ee…`), so the table name alone is not a
  label. The manifest also supplies the model each test guards and the column it
  tests. It's gitignored, so `build_tests` degrades to bare table names when it's
  absent rather than failing.
- **It excludes its own output from the inventory.** Otherwise the table count is
  10 on a first build and 13 on every later one, for no change in the warehouse.
- **It must run after `dbt build`** — it reads `dbt_test__audit` and the
  manifest, neither of which exists before one.

## The lake (`lake/archive.py`)

`just lake` writes the year-keyed tables back out of DuckDB as hive-partitioned
Parquet under `data/lake/<table>/year=<year>/data_0.parquet` (zstd, gitignored,
762 files / ~27 MB today). It's part of `just run`, an asset
(`lake/parquet_archive`) downstream of the mart, and `lake_matches_warehouse`
checks the read-back row counts and year spans against the warehouse.

- **It's an archive of the warehouse, not a landing zone in front of it.** dlt's
  filesystem destination writes Parquet but can't partition by a data column;
  DuckDB's `COPY … PARTITION_BY` can. Reversing the flow (lake first, dbt reading
  it with `read_parquet`) would mean giving up dlt's schema inference and the
  `raw` freshness checks — not worth it for a demo of file layout.
- **`overwrite true` is not enough** and the archive deletes each table's
  directory before writing. DuckDB only replaces the partitions it is *writing*,
  so a year whose last row disappeared upstream would keep answering queries out
  of a stale file. `tests/test_lake.py` pins that.
- **Rewriting from empty still leaves the output byte-identical run to run**, so
  the diff is meaningful: revising one country-year upstream changes exactly one
  of the 762 files. That is the point of the layer — the DuckDB file differs
  everywhere on every run, so it can't tell you what moved.
- **275 partitions of ~47 kB is too many small files for a real lake** (~100 MB
  per partition is the usual rule of thumb, and on object storage this would be
  275 round trips). Year is the partition column anyway because it's the one every
  query filters on, and pruning still measures: `where year = 2020` runs in ~23 ms
  against ~50 ms for the whole archive.
- **`LAKE_DIR` overrides the destination** the way `WAREHOUSE_PATH` does for the
  warehouse — `just test-pipeline` points it at a temp directory so a fixture run
  can't overwrite the real archive with the 17-country slice.

## Publishing (`scripts/export_warehouse.py`)

`just export-data` packages the built warehouse into `data/export/` (gitignored):
a `COPY FROM DATABASE` copy of the DuckDB file, a zstd Parquet per table in
`staging`/`marts`/`analytics`, `manifest.json`, `SHA256SUMS`, `ATTRIBUTION.md`
and the release body. `.github/workflows/release-data.yml` runs it monthly (and
on demand) after materializing the graph against live sources, and attaches the
lot to a dated `data-YYYY-MM-DD` GitHub release.

- **The published DuckDB file must be named `warehouse.duckdb` and attached as
  `warehouse`.** DuckDB names a catalog after the file stem, and dbt writes the
  `staging` views with fully-qualified SQL (`warehouse.raw.owid_co2`). Rename the
  file or `ATTACH … AS wh` and the views raise `Catalog "warehouse" does not
  exist` while the `marts`/`analytics` tables keep working — a confusing
  half-broken artifact. `tests/test_export.py` guards it.
- **Releases redistribute upstream data**, which the repo itself doesn't. All
  four sources are CC BY 4.0 / Eurostat reuse, so attribution is the obligation:
  `ATTRIBUTION` in the export script is the single source of truth for both the
  shipped file and the release notes. Keep it in step with the README's licence
  section when a source is added.
- **`raw` and `history` ship in the DuckDB file but not as Parquet.** The flat
  files are the modelled layers only (`PUBLISHED_SCHEMAS`); anyone who wants
  dlt's landing tables or the snapshot downloads the database. The published
  snapshot always holds one version per row anyway — the workflow builds from
  scratch.

## Conventions & gotchas (learned the hard way)

- **Clean schema names** come from `dbt/macros/generate_schema_name.sql`, which
  overrides dbt's default `<target>_<custom>` (which would give `main_marts`).
  Reference marts as `marts.fct_emissions_energy`, not `main_marts.…`.
- **dlt persists its schema and only *widens* types.** If a column lands with the
  wrong type, re-running won't fix it — the pipeline uses
  `refresh="drop_resources"` (`REFRESH` in `ingest/pipeline.py`) to force
  re-inference each run. Don't remove that without a reason. It's
  `drop_resources` and not `drop_sources` because Dagster can run a subset of the
  source: `drop_sources` would wipe the four tables that *weren't* selected.
- **Four resources `replace`, `wb_wdi` `merge`s — and that's two loads, not one.**
  `refresh` is an argument to `run()`, not a property of a resource, so a single
  run can't refresh the replace tables while leaving the incremental one alone:
  `drop_resources` would take `raw.wb_wdi` and its watermark with it. Hence
  `load_groups()` in `ingest/pipeline.py`, which both `main()` and the Dagster
  asset iterate — replace resources with `refresh=REFRESH`, then `wb_wdi`
  without. It takes the selected resource names so materialising one asset still
  runs exactly one load. Add a resource to `public_indicators()` and it must go
  in `FULL_REFRESH_RESOURCES` or `INCREMENTAL_RESOURCES` too; a test asserts the
  two cover the source exactly.
- **WDI's incremental window is 5 years, and that's about restatements.** The
  watermark (`max_year_by_indicator`, in dlt's resource state — one entry per
  indicator, so a newly added code still pulls its whole series) is *not* the
  fetch floor: `wdi_start_year()` subtracts `WDI_LOOKBACK_YEARS`, because the
  World Bank revises years it has already published. Merging on
  `(indicator, country_iso3, year)` is what makes the partial fetch safe. Two
  things it gives up, both deliberate: a country-year the World Bank *withdraws*
  stays in `raw.wb_wdi` until a full reload, and a restatement older than the
  window is never seen — `just ingest-wdi-full` (`INGEST_WDI_FULL=1`) is the
  escape hatch. dlt resets its own state when the destination is empty, so
  deleting the warehouse still gives you a full load; dropping *just* the raw
  table does not.
- **dlt state is keyed on the pipeline *name*, not the destination.** So a
  fixture run would otherwise hand its WDI watermark to the next real run, which
  would fetch a five-year window into a warehouse that has no history —
  `build_pipeline()` appends `_fixtures` to the name under `INGEST_FIXTURES=1`
  for exactly that reason. (dlt does reset state when the destination is empty,
  which is why this only bites when the real warehouse already exists.)
- **`wb_wdi`'s column types are declared, not inferred** (`WDI_COLUMNS`). It's
  the one resource whose schema isn't dropped and re-inferred each run, and
  `value` mixes counts with ratios — a lookback window that happened to hold only
  integers would infer bigint and shunt the next ratio into a
  `value__v_double` variant column.
- **Polars CSV type inference** defaults to the first 100 rows. OWID's early rows
  are empty for most metrics, so `pl.read_csv(..., infer_schema_length=None)` is
  required or numeric columns land as VARCHAR.
- **World Bank JSON is snake_cased by dlt**: API `iso2Code`/`capitalCity`/
  `incomeLevel.value` land as `iso2_code`/`capital_city`/`income_level__value`.
  Verify column names against `information_schema.columns` before writing SQL.
- **The World Bank doesn't list every ISO3 OWID emits for.** Taiwan (~286 Mt CO2,
  bigger than the Netherlands) and ten small territories arrive with a null
  `region`, so any `where region is not null` silently drops them from regional
  rollups. `dbt/seeds/country_overrides.csv` fills them in and `stg_country`
  unions it in. Antarctica is deliberately left out — a null `region` should mean
  "not a country". Coordinates use `try_cast`: the API sends `''` for territories.
- **World Bank region names are padded** — `'Sub-Saharan Africa '` and
  `'Latin America & Caribbean '` come back with a trailing space. `stg_country`
  trims them, so join and group on the trimmed values.
- **"Latest year" is per column, not per table.** `max(year)` on the mart is
  whichever source runs furthest ahead (Eurostat prices, a year beyond the rest),
  and coverage thins out unevenly before that: `co2_mt` holds 214 countries into
  the latest year, `primary_energy_twh` collapses from ~210 to **79**,
  `consumption_co2` stops a year earlier still. Cutting an energy chart to the
  latest CO2 year quietly drops two thirds of its sample. The Evidence layer
  reads `sources/warehouse/latest_years.sql` — latest year per *metric family*,
  each with its own coverage floor — instead of hardcoding a literal; add a
  family there before charting a column whose coverage curve differs.
- **`renewables_share_pct` covers 79 countries; the `*_elec` columns cover ~210.**
  OWID's broad-coverage energy series is the *electricity* mix, not the
  primary-energy mix. For anything where country coverage matters, prefer
  `low_carbon_share_elec_pct` or `carbon_intensity_elec_g_kwh` (gCO2/kWh, which
  also reads directly: coal grid ~800, gas ~400, nuclear/hydro under 50). They
  answer a narrower question — electricity is roughly a third of energy use — so
  the two are not interchangeable in levels, only in intent.
- **Territorial vs. consumption-based emissions.** `co2_mt` is what a country
  burns; `consumption_co2` adds the carbon embodied in imports and subtracts
  exports (~120 countries, one year behind). It exists so "the cut was just
  offshored" can be measured rather than caveated: the UK's territorial fall
  since 2005 is 46% and its consumption fall 36%, so about a fifth of the
  headline is trade moving and the rest isn't. `trade_co2_share` is deliberately
  untested — the real range is about -98% to +1023% (Singapore imports ten times
  what it emits), so a 0–100 bound would fail on reality, not on a bug.
- **Two carbon-intensity columns, different bases.** `fct_emissions_energy.co2_per_gdp`
  is OWID's kg CO2 per 2011 international-$ (PPP) and stops in 2022 / 164
  countries. `analytics.co2_intensity.co2_per_gdp_const_usd` is derived in
  `transform/co2_intensity.py` and tracks the mart — ~197 countries through 2024,
  but only back to 1960, where WDI starts. Levels aren't comparable between the
  two; the rank uses only the derived one.
- **Divide by `gdp_constant_usd`, never `gdp_usd`, for anything measured over
  time.** `gdp_usd` (`NY.GDP.MKTP.CD`) is *current* US$, so it moves with
  inflation and the exchange rate: on that basis Japan cut emissions 21% from
  2010–2024 and still scored 10% *worse* on carbon intensity, purely because the
  yen fell 28% against the dollar. `gdp_constant_usd` (`NY.GDP.MKTP.KD`, constant
  2015 US$) is the real-terms series. Current US$ is fine for single-year
  cross-sections, wrong for trends.
- **World Bank WDI** is fetched long (one row per indicator/country/year) and
  pivoted to wide columns in `stg_wdi.sql`. Add indicators in two places:
  `WB_WDI_INDICATORS` in `ingest/pipeline.py` and a `max(case …)` in `stg_wdi.sql`.
- **Eurostat is JSON-stat** — a flat `value` dict keyed by a row-major index over
  all dimensions. `eu_elec_prices` filters every dimension but `geo`/`time`
  server-side, then walks that grid (see `pipeline.py`). Its `geo` codes are ISO2
  *except* `EL`=Greece (GR) and `UK`=UK (GB); `stg_eu_electricity_prices.sql`
  remaps those, joins `stg_country` for ISO3, and averages the two half-years to
  annual. EU/EEA only, so the mart column is null for the rest of the world.

## Orchestration (`orchestration/`)

Dagster wraps the existing layers; it doesn't replace them. `ingest`, `dbt` and
`transform` stay independently runnable, and `orchestration/assets.py` imports
them rather than duplicating logic (`build_pipeline()`, `dbt build`,
`transform.co2_intensity.run()`).

- **Asset keys are the join between the layers.** dlt resources are keyed
  `raw/<resource>` by `RawSchemaDltTranslator` specifically to match the keys
  dagster-dbt derives from `_sources.yml`. Rename a dbt source table without
  renaming the dlt resource and the graph silently splits in two — the halves
  still run, just unconnected. Check with `dagster definitions validate` plus a
  look at the graph.
- **`orchestration/assets.py` must not use `from __future__ import annotations`.**
  Dagster inspects the `context` parameter's annotation *object*; a stringified
  annotation fails its check with a confusing "Cannot annotate `context`" error.
- **Everything runs in one process** (`in_process_executor`, and all five dlt
  resources in a single op). DuckDB takes one writer at a time, so parallel steps
  would just fight over the file lock.
- The `daily_refresh` schedule ships `STOPPED` on purpose — opening the UI
  shouldn't start hammering public APIs on a timer.
- Dagster state lives in `.dagster/` (`DAGSTER_HOME`, exported by the justfile).
  Only `dagster.yaml` is checked in.

## Testing (`tests/`)

Two tiers, and the split is the point — see [`tests/README.md`](tests/README.md).

- `just test` — mocked-payload unit tests over the ingest/transform logic. No
  network, no warehouse, ~1s.
- `just test-pipeline` — the real modules end to end with `INGEST_FIXTURES=1`,
  serving all five sources from `tests/fixtures/ingest/`. This is what CI runs,
  so a red PR build means the repo broke, not that OWID was down.

Gotchas:

- **`WAREHOUSE_PATH` overrides the DuckDB file** for `ingest`, `transform`, `lake`
  *and* dbt's profile. It must be **absolute**: dbt resolves its path from `dbt/`,
  the Python layers from the repo root. `just test-pipeline` sets it to a temp file
  — without that, a fixture run overwrites the real warehouse with the 17-country
  slice. `LAKE_DIR` is the same idea for `data/lake/`, and the recipe sets both.
- **Fixtures filter rows, never columns.** Column-trimming would let a renamed
  upstream field pass CI against a fixture that matches a `stg_` model no longer
  matching reality. The OWID fixtures are gzipped CSV, not Parquet, so they still
  go through `pl.read_csv(..., infer_schema_length=None)`.
- **`fixtures.path_for()` raises on an unmapped URL** rather than falling back to
  the network — otherwise "offline CI" quietly becomes "CI that's online
  sometimes". `tests/test_fixtures.py` asserts every URL the pipeline can build
  resolves to a file that exists.
- **dlt wraps anything a resource generator raises** in `ResourceExtractionError`,
  so tests asserting on ingest errors match that, not the underlying exception.
- **Adding a WDI indicator means re-recording** (`just record-fixtures`), on top
  of the two places listed above.
- `.github/workflows/nightly.yml` runs the same graph against the *live* sources
  daily and opens (or comments on) a `nightly-failure` issue. That's the signal
  that the fixtures have drifted from reality.

## Verifying changes

After changing ingestion or models, run the real pipeline (`just run`) and
inspect the warehouse — don't assume. Quick check:

```bash
uv run python -c "import duckdb; \
  print(duckdb.connect('data/warehouse.duckdb', read_only=True).sql(\
  'select * from marts.fct_emissions_energy limit 5').df())"
```

## Session history

Exported Claude Code session logs live in `docs/sessions/` — see the README
there. They're checked into git as a running project history.
