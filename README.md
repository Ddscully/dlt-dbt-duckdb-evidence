# Emissions, Energy & Development

[![data snapshot](https://img.shields.io/github/v/release/Ddscully/dlt-dbt-duckdb-evidence?sort=date&filter=data-*&label=data%20snapshot&color=1f6feb)](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/releases/latest)

### 📊 [**See the live dashboard →**](https://ddscully.github.io/dlt-dbt-duckdb-evidence/)

A data pipeline that turns five public feeds into figures organisations are
required to act on: **the grid carbon intensity that sits behind every company's
Scope 2 disclosure** (30 g/kWh in Norway against 717 in South Africa — a 24×
spread on the same kilowatt-hour), **what electricity actually costs in each EU
market**, and **which sourcing countries are getting cleaner rather than
dirtier**.

Underneath that it answers the question the data was assembled for: **how does a
country's energy mix relate to its emissions and its people's wellbeing?** It
pulls CO₂, energy and development figures for ~200 countries from
[Our World in Data](https://github.com/owid/co2-data), the
[World Bank](https://databank.worldbank.org/source/world-development-indicators)
and [Eurostat](https://ec.europa.eu/eurostat), cleans and joins them, derives a
few metrics, and publishes the charts. Every finding on the site names the
decision it feeds, who makes that decision, and what it costs to get wrong.

No numbers are exported by hand: the whole thing rebuilds itself from the live
sources on every push, so the site is never more than a week behind what those
organisations publish.

<sub>Nothing to install to look at the numbers, just follow the link. The rest
of this README is for people who want to run or read the pipeline — and if
you're evaluating it as work, [`docs/FOR_REVIEWERS.md`](./docs/FOR_REVIEWERS.md)
is the short version: the SLA, what a run costs, what breaks at 1000×, and what
I'd do differently.</sub>

---

Under the hood it's an end-to-end demonstration of a **modern, lightweight
data-engineering & BI stack**. Everything runs locally with `uv` against a single
DuckDB file: no cloud warehouse, no credentials, no bill.

```
dlt  ─▶  DuckDB  ─▶  dbt  ─▶  Polars  ─▶  Evidence
 EL      store     stg/marts   heavy T     BI-as-code
└──────────────── Dagster ─────────────────┘
        one asset graph, scheduled
```

Read it in whatever order suits you: [what the dashboard
shows](#published-dashboard), [the data as a download](#published-data), [how the
layers fit together](#orchestration), [how it's tested](#tests), or just
[`just setup && just run`](#quickstart).

## Stack

| Tool | Role |
|------|------|
| [**uv**](https://docs.astral.sh/uv/) | project & environment manager |
| [**dlt**](https://dlthub.com/) | EL: API/CSV ingestion into DuckDB w/ schema inference |
| [**DuckDB**](https://duckdb.org/) | in-process analytical warehouse (a single file) |
| [**dbt**](https://docs.getdbt.com/) (`dbt-duckdb`) | T: staging + marts, tests, docs |
| [**Dagster**](https://dagster.io/) | orchestration: every layer as a software-defined asset |
| [**Polars**](https://pola.rs/) | heavy columnar transforms / window logic in Python |
| **Parquet** | hive-partitioned archive of the warehouse in `data/lake/`: pruning, portability, a diffable raw layer |
| [**Evidence**](https://evidence.dev/) | BI-as-code dashboard, deployable to GitHub Pages |
| [**marimo**](https://marimo.io/) | reactive notebook for exploration |
| [**Harlequin**](https://harlequin.sh/) | terminal SQL IDE for DuckDB |
| [**sqlfluff**](https://sqlfluff.com/) + pre-commit | SQL linting / CI rigor |
| [**pytest**](https://docs.pytest.org/) | unit tests over the ingest/transform logic |
| **GitHub Actions** | fixture-backed pipeline run on every PR, live run nightly |

Want this shape for your own dataset?
[`docs/REUSING_THIS_STACK.md`](./docs/REUSING_THIS_STACK.md) covers what copies
over unchanged, what has to be rewritten, and the four decisions that are
expensive to revisit later.

## Data sources

**Five feeds from three publishers.** All country + year keyed, freely licensed,
small enough to run locally.

| Dataset | Grain | Link |
|---------|-------|------|
| OWID CO₂ & GHG | country-year (fact) | https://github.com/owid/co2-data |
| OWID Energy | country-year (fact) | https://github.com/owid/energy-data |
| World Bank WDI: GDP, life expectancy, population, poverty | country-year (fact) | https://databank.worldbank.org/source/world-development-indicators |
| World Bank countries: region & income group | country (dimension) | https://api.worldbank.org/v2/country?format=json |
| Eurostat: household electricity prices (EU/EEA) | country-half-year (fact) | https://ec.europa.eu/eurostat/databrowser/view/nrg_pc_204 |

Joins are on **ISO country code + year**, yielding marts like
*"CO₂ per \$ of GDP by income group over time"* and *"renewables adoption vs. life expectancy."*
The sources don't agree on coverage, so the fact is built on an explicit
country-year spine (`dim_country_year`) rather than off whichever source happens
to be widest. A country-year only Eurostat or only the World Bank reports still
lands, with the other columns null.

Eurostat is the one source whose own grain is finer than that, and it keeps it:
`fct_eu_electricity_prices_semiannual` holds the published half-years alongside
the annual average that joins to everything else. Averaging is what the annual
grain costs, and it costs a lot: half-over-half price moves averaged 19% across
countries in 2022 against 3–4% through the 2010s. So both are in the warehouse
rather than only the convenient one.

Four of the five load with dlt's `replace` disposition. They are small enough
that a full reload every run is the honest default, and it keeps dlt re-inferring
the schema so an upstream type change fails loudly. WDI is the counter-example:
it's the
biggest pull (~190k rows across 11 indicators) and loads with `merge` on
`(indicator, country_iso3, year)` over a five-year window. The window is a
lookback rather than "everything newer than last time" because the World Bank
restates published years, and the two dispositions load in two `run()` calls
because `refresh` is a property of a run, so refreshing the replace tables in the
same call would drop the incremental one's history along with its watermark. A
restatement older than the window doesn't need the whole series pulled again:
WDI is partitioned by year in the asset graph, so `just backfill-wdi 1997`
re-fetches exactly that year and merges it in.

## Warehouse layout

The pipeline populates one DuckDB file (`data/warehouse.duckdb`) with these schemas:

| Schema | Written by | Contents |
|--------|-----------|----------|
| `raw` | dlt | landed source tables (`owid_co2`, `owid_energy`, `wb_country`, `wb_wdi`, `eu_elec_prices`) |
| `staging` | dbt (views) | cleaned 1:1 models (`stg_*`) at `(country_iso3, year)` grain, except `stg_eu_electricity_prices_semiannual`, which keeps Eurostat's half-years |
| `marts` | dbt (tables) | `dim_country_year`, the country-year spine; `fct_emissions_energy`, the wide joined fact; `fct_co2_estimate_versions`, revision history; `fct_eu_electricity_prices_semiannual`, EU prices at their published half-year grain |
| `history` | dbt (snapshot) | `snap_co2_estimates`, SCD2 versions of OWID's CO₂ numbers and the one table a rebuild can't reproduce |
| `analytics` | Polars | derived metrics (`co2_intensity`) |

### And a lake beside it: `data/lake/`

`just lake` writes the year-keyed tables back out as hive-partitioned Parquet at
`data/lake/<table>/year=<year>/data_0.parquet`, zstd, 762 files and ~27 MB today.
Not because one DuckDB file is too small for the data, but because the file layout
buys three things the single file can't:

```sql
-- reads one 47 kB file, not the table: ~23 ms vs ~50 ms for the whole archive
select sum(co2) from read_parquet('data/lake/raw_owid_co2/**/*.parquet',
                                  hive_partitioning = 1)
where year = 2020;
```

- **Pruning.** The partition column is in the path, so `where year = …` never
  opens the other 274 files.
- **Portability.** Parquet outlives a DuckDB storage version, and every engine
  reads it.
- **A diffable raw layer.** The output is byte-identical run to run, so a
  restatement upstream shows up as exactly one changed file out of 762. The
  DuckDB file differs everywhere, every time.

It's an archive *of* the warehouse rather than a landing zone in front of it, and
the trade-off it makes is deliberate: 275 partitions averaging 47 kB is far too
many small files for a real lake on object storage. See
[CLAUDE.md](CLAUDE.md#the-lake-lakearchivepy) for why it's arranged this way.

## Orchestration

`just run` chains the steps in a shell, which works right up until you
want to know *why* a table is stale or rebuild only what a change touched.
Dagster models the same pipeline as one asset graph instead:

```
raw/owid_co2      ─┐                   ┌─▶ marts/dim_country_year ─▶ marts/fct_emissions_energy ─┬─▶ analytics/co2_intensity
raw/owid_energy   ─┤                   │           (dbt)                       (dbt)                │       (Polars)
raw/wb_country    ─┼─▶ staging/stg_* ──┤                                                            └─▶ lake/parquet_archive
raw/wb_wdi        ─┤       (dbt)       └─▶ history/snap_co2_estimates ─▶ marts/fct_co2_estimate_versions  (DuckDB → Parquet)
raw/eu_elec_prices─┘                            (dbt snapshot)                       (dbt)
      (dlt)

  ...and the four marts + analytics/co2_intensity + analytics/pipeline_status
                    └─▶ reports/evidence_site   (Evidence → static HTML)
```

Nothing declares that order by hand. The dlt resources are keyed
`raw/<resource>` to match the source keys dagster-dbt derives from
`_sources.yml`; the model-to-model edges come from dbt's own `ref()` graph via
`manifest.json`; the Polars asset names its upstream mart; the Evidence site
declares one dep per table its source queries read, and a unit test fails if a
source query starts reading a table that isn't in that list. Change a `ref()` and
the graph moves with it.

```bash
just dagster                              # UI on :3000: graph, runs, freshness, checks
just materialize                          # whole graph, headless
just materialize-site                     # ...plus the Evidence site (needs Node)
just materialize-select 'raw/wb_wdi*'     # one source + everything downstream
just backfill-wdi 1990 1995               # re-load WDI for a range of years
```

The site is the one asset held out of the `full_refresh` job: it shells out to
npm, and CI, the nightly run and the data release all want a graph that runs on a
bare Python checkout. `publish_site` is `full_refresh` plus the site, and it's
what the Pages workflow runs.

What that buys over the shell chain:

| | |
|---|---|
| **Selective rebuilds** | `raw/wb_wdi*` reloads one API and rebuilds only what depends on it. dlt refreshes just that resource, so its four siblings keep their data. (`*` is all downstream; a bare `+` is only one layer.) |
| **Re-runnable backfills** | `raw/wb_wdi` is partitioned by year (1960 → now), so a World Bank restatement older than the five-year lookback is a unit of work you can point at instead of a 190k-row full reload. A range is one request per indicator, and `merge` on `(indicator, country_code, year)` makes re-running a year a no-op. It's the only partitioned asset: the other four sources are whole-file downloads with no per-year fetch to express. |
| **Freshness policies** | Raw assets warn after 2 days and fail after 7; modelled assets are expected by 08:00 UTC daily. A schedule that quietly stops firing turns assets stale in the UI instead of leaving no trace. |
| **Asset checks** | dbt's `not_null` tests show up as checks on the model they guard, next to Python checks dbt can't express (every WDI indicator present, mart reaching a recent year, dense ranks with no gaps). |
| **Lineage that can't drift** | The graph is derived from the dbt manifest and the dlt source, not maintained alongside them. |

A `daily_refresh` schedule (06:00 UTC) is defined but ships **stopped**. Start it
from the UI if you want it running.

## Layout

```
.
├── src/modern_data_stack/ # the domain-neutral half: paths, fixtures, lake,
│                      #   observability, export, snapshot carry-forward
├── ingest/            # dlt pipeline: sources -> DuckDB (schema `raw`)
├── orchestration/     # Dagster: the pipeline as an asset graph + schedule
├── dbt/               # dbt-duckdb project
│   ├── models/staging # 1:1 cleaned source views (stg_*)
│   ├── models/marts   # the country-year spine (dim_*) + the facts (fct_*)
│   ├── snapshots/     # SCD2 history of OWID's CO2 estimates (schema `history`)
│   └── macros/        # generate_schema_name -> clean schema names
├── transform/         # Polars: derived metrics -> schema `analytics`
├── lake/              # DuckDB -> hive-partitioned Parquet in data/lake/
├── tests/             # pytest + the recorded API fixtures CI runs against
├── scripts/           # record_fixtures.py, export_warehouse.py (the release)
├── notebooks/         # marimo reactive notebooks
├── reports/           # Evidence dashboard (BI as code)
├── docs/STYLE_GUIDE.md # SQL + model conventions (the lint rules and the rest)
├── docs/REUSING_THIS_STACK.md # starting a different project on this shape
├── docs/sessions/     # exported Claude Code session logs (project history)
├── data/              # warehouse.duckdb lives here (gitignored)
├── justfile           # orchestration recipes
├── CLAUDE.md          # guidance for Claude Code / contributors
├── .claude/           # agent skills for this repo + vendor plugin declarations
└── .github/workflows  # CI
```

## Working on this with an AI agent

`.claude/settings.json` declares the official agent-skill plugins for the stack,
[dbt](https://github.com/dbt-labs/dbt-agent-skills),
[Dagster](https://github.com/dagster-io/skills),
[Polars](https://github.com/polars-inc/skills) and
[DuckDB](https://github.com/duckdb/duckdb-skills), so Claude Code offers to
install them the first time you trust the repo. `.claude/skills/` adds three
project-specific skills covering the seams between layers, and
[`docs/STYLE_GUIDE.md`](./docs/STYLE_GUIDE.md) holds the conventions. See
[CLAUDE.md](./CLAUDE.md#agent-skills) for the full picture.

## Quickstart

```bash
just setup      # uv sync runtime + dev + notebook + orchestration groups
just run        # ingest -> dbt build -> polars transform
just dagster    # ...or the same pipeline as an asset graph, UI on :3000
just sql        # poke around the warehouse in Harlequin
just notebook   # marimo exploration
just report     # build the Evidence dashboard
```

No `just`? The recipes map to plain commands; see the [`justfile`](./justfile).

## Tests

```bash
just test           # pytest: mocked payloads, no network, ~1s
just test-pipeline  # the whole pipeline against recorded fixtures, ~30s
```

Contributors should also run `uv run pre-commit install` once, for the ruff, SQL
lint and whitespace hooks. CI runs the same hooks over every file, so a PR that skips
them fails there instead.

CI on a pull request runs both, plus the Dagster asset graph and the asset
checks, entirely offline: `INGEST_FIXTURES=1` serves all five sources from
`tests/fixtures/ingest/`. A red build therefore means *this repo* broke, not that
OWID was rate-limiting. A separate nightly workflow runs the same graph against
the live endpoints and opens an issue when a source has moved, which is the cue
to fix the pipeline and `just record-fixtures`. Details in
[`tests/README.md`](./tests/README.md).

### Data-quality gates

`just dbt-build` runs 113 dbt tests alongside the models, and Dagster surfaces
each one as an asset check on the model it guards:

| Gate | What it catches |
|------|-----------------|
| `dbt_utils.unique_combination_of_columns` on `(country_iso3, year)` | The grain contract, on every fact-shaped staging model, the spine and the mart. `fct_emissions_energy` is four left joins off `dim_country_year`, so one duplicated upstream row would fan the mart out silently. |
| `dbt_utils.accepted_range` | Percentages inside 0–100, non-negative money and tonnage, years inside each source's real span (WDI starts in 1960, Eurostat in 2007), EU electricity under €1/kWh. Unit and index-arithmetic bugs land outside these long before anyone notices a wrong chart. |
| `not_null` / `unique` / `accepted_values` | The country dimension: one row per ISO3, a region for every row, income groups from the World Bank's four. |
| `dbt source freshness` (`just dbt-freshness`) | Whether the warehouse is stale. dlt stamps every row with `_dlt_load_id`, a unix epoch, so this measures when the *pipeline* last ran (warn at 7 days, error at 30) rather than when the publishers last updated. |

The tests are deliberately calibrated to fail on a bug rather than on reality:
`income_group` is left nullable because the `country_overrides` territories
genuinely have no World Bank classification, and `co2_per_capita` has a floor but
no ceiling because small petrostates legitimately reach 780 t/person.

## Published dashboard

### 👉 [ddscully.github.io/dlt-dbt-duckdb-evidence](https://ddscully.github.io/dlt-dbt-duckdb-evidence/)

Five pages, built from the modelled layers: `marts.fct_emissions_energy` and
`analytics.co2_intensity` for the findings, plus `dim_country_year`,
`fct_co2_estimate_versions`, `fct_eu_electricity_prices_semiannual` and the
`analytics.pipeline_*` tables for the rest. No year is hardcoded: every page reads
the latest year each metric family can actually populate from
`sources/warehouse/latest_years.sql`, because coverage doesn't end in the same year
for all of them.

| Page | What's on it |
|------|--------------|
| **Home**: Explore | Pick a year: clean electricity vs. life expectancy, carbon intensity of the economy over time, grid carbon intensity, EU electricity prices against grid cleanliness, the most carbon-efficient economies, and what averaging Eurostat's half-year prices into an annual figure costs. |
| **Findings** | Seven write-ups on the joined data: when each country's emissions peaked, that the cleanup happened in electricity and coal is most of it, real-terms decoupling, whether it's just offshoring (it isn't, mostly), emissions tracking income rather than headcount, cumulative vs. current responsibility, and carbon intensity falling while absolute tonnes rise. |
| **Coverage** | Which series actually cover which countries, by left-joining the fact onto the country-year spine so a gap is a row. Names both populations that break naive queries: territories with World Bank data and no OWID emissions, and countries with emissions and no World Bank GDP (Taiwan leads at 262 Mt, so it is silently absent from every intensity measure). |
| **Pipeline** | dlt load times per source, rows and year spans per layer, and all 113 dbt tests with their stored failure counts, from the observability tables that `transform/pipeline_status.py` writes. |
| **Restatements** | Which CO₂ estimates OWID has revised since this warehouse first loaded them, off the dbt snapshot. Empty on the published copy by construction: the build starts from an empty DuckDB file, and a snapshot can only record a revision it was there for. |

`.github/workflows/pages.yml` builds it, as a single `publish_site` job. The site
is a node in the asset graph (`reports/evidence_site`), so the workflow
materializes it rather than running npm itself. It runs the pipeline against the
**live** sources rather than the fixtures (a published dashboard showing the
17-country test slice would be worse than none), on every push to `main`,
weekly, and on demand, so the site is never more than a week behind whatever
OWID and the World Bank are publishing.

Three things to know if you're copying this setup:

- Pages has to be enabled once by hand: **Settings → Pages → Source → GitHub
  Actions**.
- **`evidence build` does not run the sources.** It renders against whatever
  parquet `reports/.evidence/` already holds, which locally is a warm cache and
  in CI is nothing at all, so `sources:strict` has to run first. Skip it and you
  deploy a perfectly working site where every chart says *Table with name
  emissions_energy does not exist*. That ordering lives in
  `scripts/build_report.py`, which is the single implementation behind both
  `just report` and the asset.
- Project Pages serve from a subpath, so the workflow appends
  `deployment.basePath` to `reports/evidence.config.yaml` at build time. It's
  injected rather than committed because a base path set in the file also
  applies to `npm run dev`, which breaks local preview on `localhost:3000`.

## Published data

### 👉 [Latest snapshot](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/releases/latest)

The dashboard is one consumer of the warehouse; the warehouse itself is
published too, so you can use the joined data without running any of this.
Each release carries the whole DuckDB file plus a Parquet per modelled table,
`manifest.json` (row counts, year coverage, SHA-256 per asset) and `SHA256SUMS`.

Query it where it sits. DuckDB reads a remote database over HTTPS:

```sql
INSTALL httpfs; LOAD httpfs;
ATTACH 'https://github.com/Ddscully/dlt-dbt-duckdb-evidence/releases/latest/download/warehouse.duckdb'
       AS warehouse (READ_ONLY);
SELECT country_name, year, co2_mt, renewables_share_pct
FROM warehouse.marts.fct_emissions_energy
WHERE year = 2024;
```

Or take a single table as a flat file, no DuckDB required:

```sql
SELECT * FROM read_parquet('https://github.com/Ddscully/dlt-dbt-duckdb-evidence/releases/latest/download/marts__fct_emissions_energy.parquet');
```

`.github/workflows/release-data.yml` builds it from the live sources monthly (the
publishers update annually) or on demand, and `scripts/export_warehouse.py`
packages it. `just export-data` does the same thing locally, into `data/export/`.
Tags are dated, `data-YYYY-MM-DD`; `releases/latest/download/…` always resolves
to the newest one, so the URLs above never go stale.

Three things to know if you're copying this setup:

- **Alias it `warehouse`.** dbt writes the `staging` views with fully-qualified
  SQL, and DuckDB names a catalog after its file, so those views only resolve
  under that name. `ATTACH … AS wh` reads the `marts` and `analytics` *tables*
  fine and makes every view raise `Catalog "warehouse" does not exist`. Same
  reason the export copies the file as `warehouse.duckdb` rather than
  `snapshot-2026-07-30.duckdb`.
- **The copy is made with `COPY FROM DATABASE`, not `cp`.** It's consistent
  whatever state the source was left in (a crashed run leaves a `.wal` beside
  it) and compacted, which is most of why 32 MB of warehouse ships as 29 MB.
- **The DuckDB file has a storage format**; it was written by whatever version
  the workflow resolved, recorded in `manifest.json`. Older clients may refuse
  it. The Parquet files have no such constraint, which is why both ship.
- **`history` is inherited, not rebuilt.** Everything else in the file is built
  from scratch each time, but the SCD2 snapshot of OWID's CO₂ estimates is the
  one table that can't be — a revision only leaves a trace if you were holding
  the previous number. So each release downloads its predecessor and copies
  `history` in before it builds (`scripts/restore_history.py`), and the releases
  accumulate a genuine revision log. `manifest.json` reports how much of one.

## License

Code is [MIT](./LICENSE). The data is not this project's to license: OWID's
[CO₂](https://github.com/owid/co2-data) and
[energy](https://github.com/owid/energy-data) datasets are CC BY 4.0, World Bank
WDI is CC BY 4.0, and Eurostat data carries its own
[reuse policy](https://ec.europa.eu/eurostat/about-us/policies/copyright).

All four permit redistribution with attribution, which is what the data releases
above rely on. Every one ships an `ATTRIBUTION.md` naming the publisher and
licence per source, and the release notes repeat it. Attribute them, not this
repo, for the numbers; the joins and derived metrics are the only part that's
ours. Nothing upstream is redistributed in the repository *itself*: the pipeline
fetches it at run time, and the checked-in fixtures under
`tests/fixtures/ingest/` are small excerpts kept for offline testing.
