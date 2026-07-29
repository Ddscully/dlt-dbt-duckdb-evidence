# Emissions, Energy & Development

[![data snapshot](https://img.shields.io/github/v/release/Ddscully/dlt-dbt-duckdb-evidence?sort=date&filter=data-*&label=data%20snapshot&color=1f6feb)](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/releases/latest)

### 📊 [**See the live dashboard →**](https://ddscully.github.io/dlt-dbt-duckdb-evidence/)

A data pipeline that answers one question: **how does a country's energy mix
relate to its emissions and its people's wellbeing?**

It pulls CO₂, energy and development figures for ~200 countries from
[Our World in Data](https://github.com/owid/co2-data), the
[World Bank](https://databank.worldbank.org/source/world-development-indicators)
and [Eurostat](https://ec.europa.eu/eurostat), cleans and joins them, derives a
few metrics, and publishes the charts. No numbers are exported by hand: the
whole thing rebuilds itself from the live sources on every push, so the site is
never more than a week behind what those organisations publish.

<sub>Nothing to install to look at the numbers — just follow the link. The rest
of this README is for people who want to run or read the pipeline.</sub>

---

Under the hood it's an end-to-end demonstration of a **modern, lightweight
data-engineering & BI stack**. Everything runs locally with `uv` against a single
DuckDB file — no cloud warehouse, no credentials, no bill.

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
| [**dlt**](https://dlthub.com/) | EL — API/CSV ingestion into DuckDB w/ schema inference |
| [**DuckDB**](https://duckdb.org/) | in-process analytical warehouse (a single file) |
| [**dbt**](https://docs.getdbt.com/) (`dbt-duckdb`) | T — staging + marts, tests, docs |
| [**Dagster**](https://dagster.io/) | orchestration — every layer as a software-defined asset |
| [**Polars**](https://pola.rs/) | heavy columnar transforms / window logic in Python |
| [**Evidence**](https://evidence.dev/) | BI-as-code dashboard, deployable to GitHub Pages |
| [**marimo**](https://marimo.io/) | reactive notebook for exploration |
| [**Harlequin**](https://harlequin.sh/) | terminal SQL IDE for DuckDB |
| [**sqlfluff**](https://sqlfluff.com/) + pre-commit | SQL linting / CI rigor |
| [**pytest**](https://docs.pytest.org/) | unit tests over the ingest/transform logic |
| **GitHub Actions** | fixture-backed pipeline run on every PR, live run nightly |

## Data sources

All country + year keyed, freely licensed, small enough to run locally.

| Dataset | Grain | Link |
|---------|-------|------|
| OWID CO₂ & GHG | country-year (fact) | https://github.com/owid/co2-data |
| OWID Energy | country-year (fact) | https://github.com/owid/energy-data |
| World Bank WDI — GDP, life expectancy, population, poverty | country-year (fact) | https://databank.worldbank.org/source/world-development-indicators |
| World Bank countries — region & income group | country (dimension) | https://api.worldbank.org/v2/country?format=json |

Joins are on **ISO country code + year**, yielding marts like
*"CO₂ per \$ of GDP by income group over time"* and *"renewables adoption vs. life expectancy."*

## Warehouse layout

The pipeline populates one DuckDB file (`data/warehouse.duckdb`) with these schemas:

| Schema | Written by | Contents |
|--------|-----------|----------|
| `raw` | dlt | landed source tables (`owid_co2`, `owid_energy`, `wb_country`, `wb_wdi`) |
| `staging` | dbt (views) | cleaned 1:1 models (`stg_*`) at `(country_iso3, year)` grain |
| `marts` | dbt (tables) | `fct_emissions_energy` — the wide joined fact |
| `analytics` | Polars | derived metrics (`co2_intensity`) |

## Orchestration

`just run` chains the three steps in a shell, which works right up until you
want to know *why* a table is stale or rebuild only what a change touched.
Dagster models the same pipeline as one asset graph instead:

```
raw/owid_co2      ─┐
raw/owid_energy   ─┤
raw/wb_country    ─┼─▶ staging/stg_*  ─▶  marts/fct_emissions_energy  ─▶  analytics/co2_intensity
raw/wb_wdi        ─┤        (dbt)                    (dbt)                       (Polars)
raw/eu_elec_prices─┘
      (dlt)
```

Nothing declares that order by hand. The dlt resources are keyed
`raw/<resource>` to match the source keys dagster-dbt derives from
`_sources.yml`; the model-to-model edges come from dbt's own `ref()` graph via
`manifest.json`; the Polars asset names its upstream mart. Change a `ref()` and
the graph moves with it.

```bash
just dagster                              # UI on :3000 — graph, runs, freshness, checks
just materialize                          # whole graph, headless
just materialize-select 'raw/wb_wdi*'     # one source + everything downstream
```

What that buys over the shell chain:

| | |
|---|---|
| **Selective rebuilds** | `raw/wb_wdi*` reloads one API and rebuilds only what depends on it. dlt refreshes just that resource, so its four siblings keep their data. (`*` is all downstream; a bare `+` is only one layer.) |
| **Freshness policies** | Raw assets warn after 2 days and fail after 7; modelled assets are expected by 08:00 UTC daily. A schedule that quietly stops firing turns assets stale in the UI instead of leaving no trace. |
| **Asset checks** | dbt's `not_null` tests show up as checks on the model they guard, next to Python checks dbt can't express (every WDI indicator present, mart reaching a recent year, dense ranks with no gaps). |
| **Lineage that can't drift** | The graph is derived from the dbt manifest and the dlt source, not maintained alongside them. |

A `daily_refresh` schedule (06:00 UTC) is defined but ships **stopped** — start
it from the UI if you want it running.

## Layout

```
.
├── ingest/            # dlt pipeline: sources -> DuckDB (schema `raw`)
├── orchestration/     # Dagster: the pipeline as an asset graph + schedule
├── dbt/               # dbt-duckdb project
│   ├── models/staging # 1:1 cleaned source views (stg_*)
│   ├── models/marts   # joined fact/dim tables (fct_*)
│   └── macros/        # generate_schema_name -> clean schema names
├── transform/         # Polars: derived metrics -> schema `analytics`
├── tests/             # pytest + the recorded API fixtures CI runs against
├── scripts/           # record_fixtures.py, export_warehouse.py (the release)
├── notebooks/         # marimo reactive notebooks
├── reports/           # Evidence dashboard (BI as code)
├── docs/STYLE_GUIDE.md # SQL + model conventions (the lint rules and the rest)
├── docs/sessions/     # exported Claude Code session logs (project history)
├── data/              # warehouse.duckdb lives here (gitignored)
├── justfile           # orchestration recipes
├── CLAUDE.md          # guidance for Claude Code / contributors
├── .claude/           # agent skills for this repo + vendor plugin declarations
└── .github/workflows  # CI
```

## Working on this with an AI agent

`.claude/settings.json` declares the official agent-skill plugins for the stack —
[dbt](https://github.com/dbt-labs/dbt-agent-skills),
[Dagster](https://github.com/dagster-io/skills),
[Polars](https://github.com/polars-inc/skills) and
[DuckDB](https://github.com/duckdb/duckdb-skills) — so Claude Code offers to
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

No `just`? The recipes map to plain commands — see the [`justfile`](./justfile).

## Tests

```bash
just test           # pytest — mocked payloads, no network, ~1s
just test-pipeline  # the whole pipeline against recorded fixtures, ~30s
```

Contributors should also run `uv run pre-commit install` once — ruff, SQL lint
and whitespace hooks. CI runs the same hooks over every file, so a PR that skips
them fails there instead.

CI on a pull request runs both, plus the Dagster asset graph and the asset
checks, entirely offline: `INGEST_FIXTURES=1` serves all five sources from
`tests/fixtures/ingest/`. A red build therefore means *this repo* broke, not that
OWID was rate-limiting. A separate nightly workflow runs the same graph against
the live endpoints and opens an issue when a source has moved — which is the cue
to fix the pipeline and `just record-fixtures`. Details in
[`tests/README.md`](./tests/README.md).

### Data-quality gates

`just dbt-build` runs 60 dbt tests alongside the models, and Dagster surfaces
each one as an asset check on the model it guards:

| Gate | What it catches |
|------|-----------------|
| `dbt_utils.unique_combination_of_columns` on `(country_iso3, year)` | The grain contract, on every fact-shaped staging model and the mart. `fct_emissions_energy` is four left joins off `stg_co2`, so one duplicated upstream row would fan the mart out silently. |
| `dbt_utils.accepted_range` | Percentages inside 0–100, non-negative money and tonnage, years inside each source's real span (WDI starts in 1960, Eurostat in 2007), EU electricity under €1/kWh. Unit and index-arithmetic bugs land outside these long before anyone notices a wrong chart. |
| `not_null` / `unique` / `accepted_values` | The country dimension: one row per ISO3, a region for every row, income groups from the World Bank's four. |
| `dbt source freshness` (`just dbt-freshness`) | Whether the warehouse is stale. dlt stamps every row with `_dlt_load_id`, a unix epoch, so this measures when the *pipeline* last ran (warn at 7 days, error at 30) rather than when the publishers last updated. |

The tests are deliberately calibrated to fail on a bug rather than on reality:
`income_group` is left nullable because the `country_overrides` territories
genuinely have no World Bank classification, and `co2_per_capita` has a floor but
no ceiling because small petrostates legitimately reach 780 t/person.

## Published dashboard

### 👉 [ddscully.github.io/dlt-dbt-duckdb-evidence](https://ddscully.github.io/dlt-dbt-duckdb-evidence/)

Two pages, both built from `marts.fct_emissions_energy` and
`analytics.co2_intensity`:

| Page | What's on it |
|------|--------------|
| **Overview** | Pick a year: renewables share vs. life expectancy for every country (bubbles sized by population, coloured by income group), CO₂ intensity by income group over time, and the headline counts. |
| **Five findings** | The things the data actually says once it's joined — including that rich countries cut emissions while growing, and how far the energy/longevity relationship flattens out at the top. |

`.github/workflows/pages.yml` builds it. It runs the pipeline against the
**live** sources rather than the fixtures (a published dashboard showing the
17-country test slice would be worse than none), on every push to `main`,
weekly, and on demand — so the site is never more than a week behind whatever
OWID and the World Bank are publishing.

Three things to know if you're copying this setup:

- Pages has to be enabled once by hand: **Settings → Pages → Source → GitHub
  Actions**.
- **`evidence build` does not run the sources.** It renders against whatever
  parquet `reports/.evidence/` already holds, which locally is a warm cache and
  in CI is nothing at all — so the workflow runs `npm run sources:strict` first.
  Skip it and you deploy a perfectly working site where every chart says
  *Table with name emissions_energy does not exist*. `just report` runs both.
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

Query it where it sits — DuckDB reads a remote database over HTTPS:

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
packages it — `just export-data` does the same thing locally, into `data/export/`.
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

## License

Code is [MIT](./LICENSE). The data is not this project's to license: OWID's
[CO₂](https://github.com/owid/co2-data) and
[energy](https://github.com/owid/energy-data) datasets are CC BY 4.0, World Bank
WDI is CC BY 4.0, and Eurostat data carries its own
[reuse policy](https://ec.europa.eu/eurostat/about-us/policies/copyright).

All four permit redistribution with attribution, which is what the data releases
above rely on — every one ships an `ATTRIBUTION.md` naming the publisher and
licence per source, and the release notes repeat it. Attribute them, not this
repo, for the numbers; the joins and derived metrics are the only part that's
ours. Nothing upstream is redistributed in the repository *itself*: the pipeline
fetches it at run time, and the checked-in fixtures under
`tests/fixtures/ingest/` are small excerpts kept for offline testing.
