# A Public-Data Warehouse, Built End to End

*dlt → DuckDB → dbt → Polars → Evidence, orchestrated by Dagster. Rebuilt from
live sources on every push, and the warehouse itself is published, not just the
dashboard.*

[![ci](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/actions/workflows/ci.yml/badge.svg)](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/actions/workflows/ci.yml)
[![nightly](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/actions/workflows/nightly.yml/badge.svg)](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/actions/workflows/nightly.yml)
[![pages](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/actions/workflows/pages.yml/badge.svg)](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/actions/workflows/pages.yml)
[![data snapshot](https://img.shields.io/github/v/release/Ddscully/dlt-dbt-duckdb-evidence?sort=date&filter=data-*&label=data%20snapshot&color=1f6feb)](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/releases/latest)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](./LICENSE)

### 📊 [**See the live dashboard →**](https://ddscully.github.io/dlt-dbt-duckdb-evidence/)

[![The Evidence dashboard: the Eight Findings page, with the site navigation, three headline figures and a scatter of the year each large emitter's CO₂ peaked](./docs/assets/dashboard.png)](https://ddscully.github.io/dlt-dbt-duckdb-evidence/findings)

A data pipeline over seven public feeds, built at **two grains that are usually
two different projects** — and deliberately kept in one warehouse, because the
modelling problems they pose are opposite.

**Country-year: figures organisations are required to act on.** The grid carbon
intensity that sits behind every company's Scope 2 disclosure (30 g/kWh in Norway
against 717 in South Africa, a 24× spread on the same kilowatt-hour), what a
tonne of imported steel will cost at the EU border from 2026, and what
electricity actually costs in each EU market. Underneath those is the question
the data was assembled for: how does a country's energy mix relate to its
emissions and its people's wellbeing? CO₂, energy and development figures for
~200 countries from [Our World in Data](https://github.com/owid/co2-data), the
[World Bank](https://databank.worldbank.org/source/world-development-indicators),
[Eurostat](https://ec.europa.eu/eurostat) and the
[ECB](https://frankfurter.dev), cleaned, joined on ISO code and year, and
charted. Here the hard part is coverage: which country is missing from which
series, and what a join quietly drops.

**Transaction grain: the questions annual averages cannot ask.** One UK
wholesaler's complete 1.07M-line invoice log
([UCI Online Retail II](https://archive.ics.uci.edu/dataset/502/online+retail+ii)),
the finest grain in the warehouse and the only source below a country. Revenue
depends on telling a stock write-off from a customer return when both are a
negative quantity; retention is a cohort triangle; returns are matched to the
sale that produced them by inference, because nothing in the data links them. It
is also the only source with a person in it, which is why
[`docs/DATA_PROTECTION.md`](./docs/DATA_PROTECTION.md) exists.

Every finding on the site names the decision it feeds, who makes that decision,
and what it costs to get wrong.

No numbers are exported by hand. The whole thing rebuilds itself from the live
sources on every push, so the site is never more than a week behind what those
organisations publish.

<sub>Nothing to install to look at the numbers, just follow the link. The rest of
this README is for people who want to run or read the pipeline, and if you're
evaluating it as work, [`docs/FOR_REVIEWERS.md`](./docs/FOR_REVIEWERS.md) is the
short version: the SLA, what a run costs, what breaks at 1000×, and what I'd do
differently.</sub>

---

The stack is deliberately **lightweight**. Everything runs locally with `uv`
against a single DuckDB file: no cloud warehouse, no credentials, no bill.

```
dlt  ─▶  DuckDB  ─▶  dbt  ─▶  Polars  ─▶  Evidence
 EL      store     stg/marts   heavy T     BI-as-code
└──────────────── Dagster ─────────────────┘
        one asset graph, scheduled
```

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
| [**DuckLake**](https://ducklake.select/) | the same Parquet with a catalog, in `data/lakehouse/`: snapshots and a row-level change feed over the weather tables |
| [**Evidence**](https://evidence.dev/) | BI-as-code dashboard, deployable to GitHub Pages |
| [**sqlfluff**](https://sqlfluff.com/) + pre-commit | SQL linting / CI rigor |
| [**pytest**](https://docs.pytest.org/) | unit tests over the ingest/transform logic |
| **GitHub Actions** | fixture-backed pipeline run on every PR, live run nightly |

Want this shape for your own dataset?
[`docs/REUSING_THIS_STACK.md`](./docs/REUSING_THIS_STACK.md) covers what copies
over unchanged, what has to be rewritten, and the four decisions that are
expensive to revisit later.

## The docs

The README is the tour. The detail lives in six files:

| | |
|---|---|
| [`docs/WAREHOUSE.md`](./docs/WAREHOUSE.md) | the seven sources, their grains, the schemas they land in, and the Parquet lake beside them |
| [`docs/ORCHESTRATION.md`](./docs/ORCHESTRATION.md) | the Dagster asset graph, the three jobs, backfills and freshness policies |
| [`docs/DATA_QUALITY.md`](./docs/DATA_QUALITY.md) | the 425 dbt tests, the mart contracts, and the groups, exposures and model versions around them |
| [`docs/PUBLISHED_DATA.md`](./docs/PUBLISHED_DATA.md) | the monthly data release and how to query it without cloning anything |
| [`docs/DATA_PROTECTION.md`](./docs/DATA_PROTECTION.md) | the one personal column: how it is classified, what the release does to it, and how identifiable a customer stays without it |
| [`docs/FOR_REVIEWERS.md`](./docs/FOR_REVIEWERS.md) | SLA, run cost, what breaks at 1000×, what I'd do differently |

And [`docs/course/`](./docs/course/) teaches the same warehouse as material for
analytics engineers, built around the failures that stay green — a one-word join
edit that drops two thirds of the countries with all 425 tests still passing, a
cross-section that silently loses 115 of 205 countries. Modules 00–04 are
written; 05–10 are outlined in the course index. It has its own sandbox
(`just course-sandbox`) so the exercises can break things.

Plus [`docs/STYLE_GUIDE.md`](./docs/STYLE_GUIDE.md) for SQL and model
conventions, [`tests/README.md`](./tests/README.md) for the two test tiers,
[`reports/README.md`](./reports/README.md) for the Evidence layer, and
[`CLAUDE.md`](./CLAUDE.md) for every gotcha that cost more than an hour.

## What's in it

Seven feeds from five publishers, joined on **ISO country code + year**, plus one
EU regulatory annex that arrives as a seed. Two decades of national emissions,
energy mix, GDP and life expectancy; EU household electricity prices at their
published half-year grain; the ECB's daily euro reference rates back to 1999; and
one UK wholesaler's complete 1.07M-line transaction log, which is the only source
here below country grain.

Everything lands in one DuckDB file across five schemas: `raw` (dlt), `staging`
and `marts` (dbt), `history` (dbt snapshots, the only tables a rebuild can't
reproduce) and `analytics` (Polars). Full detail in
[`docs/WAREHOUSE.md`](./docs/WAREHOUSE.md).

The facts hang off an explicit country-year spine, `dim_country_year`, and not
off whichever source happens to be widest. That's what makes coverage answerable:
left-join the fact onto the spine and a gap comes back as a row you can count.

## Orchestration

`just run` chains the steps in a shell. Dagster models the same pipeline as one
asset graph, which is what lets you rebuild only what a change touched and see
why a table is stale.

```bash
just dagster                              # UI on :3000: graph, runs, freshness, checks
just materialize                          # whole graph, headless
just materialize-site                     # ...plus the Evidence site (needs Node)
just materialize-select 'raw/wb_wdi*'     # one source + everything downstream
just backfill-wdi 1990 1995               # re-load WDI for a range of years
```

Nothing declares the order by hand: dlt resource keys match the source keys
dagster-dbt derives from `_sources.yml`, the model edges come from dbt's own
`ref()` graph, and the Evidence site declares one dep per table its queries read.
See [`docs/ORCHESTRATION.md`](./docs/ORCHESTRATION.md) for the graph, the three
jobs and the partitioned WDI backfill.

## Layout

```
.
├── src/modern_data_stack/ # the domain-neutral half: paths, fixtures, lake,
│                      #   ducklake, observability, export, carry-forward
├── ingest/            # dlt pipeline: sources -> DuckDB (schema `raw`)
├── orchestration/     # Dagster: the pipeline as an asset graph + schedule
├── dbt/               # dbt-duckdb project
│   ├── models/staging # 1:1 cleaned source views (stg_*)
│   ├── models/marts   # the country-year spine (dim_*) + the facts (fct_*)
│   ├── snapshots/     # SCD2 history: CO2 estimates + grid factors (schema `history`)
│   └── macros/        # generate_schema_name -> clean schema names
├── transform/         # Polars: derived metrics -> schema `analytics`
├── lake/              # DuckDB -> hive-partitioned Parquet in data/lake/,
│                      #   and a DuckLake catalog in data/lakehouse/
├── tests/             # pytest + the recorded API fixtures CI runs against
├── scripts/           # record_fixtures.py, export_warehouse.py (the release)
├── reports/           # Evidence dashboard (BI as code)
├── docs/              # the topic docs above, plus the style guide
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
[Polars](https://github.com/polars-inc/skills),
[DuckDB](https://github.com/duckdb/duckdb-skills) and
[Astral](https://github.com/astral-sh/claude-code-plugins) (uv, ruff and ty), so
Claude Code offers to install them the first time you trust the repo.
`.claude/skills/` adds project-specific skills covering the seams the vendor
ones can't know about — adding a source across five layers, the retail and
compliance domains, and the Evidence layer, which has no vendor skill at all.
See [CLAUDE.md](./CLAUDE.md#agent-skills) for the full picture.

## Quickstart

Two things to install first. [uv](https://docs.astral.sh/uv/) manages Python and
every dependency here; `just` runs the recipes, and uv is the tidiest way to get
it — no system package manager, and it lands in the same place as your other uv
tools:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # or `brew install uv`
uv tool install rust-just                         # the `just` command runner
```

Then:

```bash
just setup      # uv sync runtime + dev + orchestration groups
just run        # ingest -> dbt build -> polars transform
just dagster    # ...or the same pipeline as an asset graph, UI on :3000
just sql        # poke around the warehouse in the DuckDB CLI
just report     # build the Evidence dashboard
```

No credentials at any point — every source is a public endpoint, and nothing but
uv and `just` has to exist on the machine first: uv reads `.python-version` and
fetches CPython 3.13 itself if you haven't got it.

For scale: the whole asset graph *including* the Evidence site took **3 minutes**
here from a cold cache, most of it the one-off 45 MB retail workbook download,
and `just run` on its own is ~95 s. Budget **~2.5 GB** on disk once everything is
built — the venv is 807 MB and `reports/node_modules` another 694 MB, with the
warehouse, the Parquet lake and the built site making up the rest. All of it is
gitignored and all of it is regenerable: `just clean` reclaims the build output,
`just clean deep` also drops `node_modules`.

**On a plane, or don't want to hit seven public APIs?** `just test-pipeline`
runs the entire pipeline offline in ~30 s against the fixtures in
`tests/fixtures/ingest/`, into a throwaway warehouse — it is what CI runs.
`just course-sandbox` does the same into a warehouse that persists, at
`data/course/`, which is what the course exercises are built to break.

One optional extra, wanted by exactly one recipe, so install it when you reach
for it rather than up front:

| | Needed by | Install |
|---|---|---|
| [Node](https://nodejs.org/) ≥ 18 | `just report`, `just materialize-site` | your package manager |

`just sql` needs no separate install — the DuckDB CLI comes from the
`duckdb-cli` dev dependency `just setup` already pulled in, pinned in
`uv.lock` alongside the `duckdb` Python library.

No `just`? The recipes map to plain commands; see the [`justfile`](./justfile).

## Tests

```bash
just test           # pytest: mocked payloads, no network, ~14s
just coverage       # the same, with line + branch coverage; gates nothing
just test-pipeline  # the whole pipeline against recorded fixtures, ~30s
```

Contributors should also run `uv run pre-commit install` once, for the ruff, SQL
lint and whitespace hooks. CI runs the same hooks over every file, so a PR that
skips them fails there instead.

CI on a pull request runs both, plus the Dagster asset graph and the asset
checks, entirely offline: `INGEST_FIXTURES=1` serves all seven sources from
`tests/fixtures/ingest/`. A red build therefore means *this repo* broke, not that
OWID was rate-limiting. A separate nightly workflow runs the same graph against
the live endpoints and opens an issue when a source has moved, which is the cue
to fix the pipeline and `just record-fixtures`. Details in
[`tests/README.md`](./tests/README.md).

Alongside them, `just dbt-build` runs 425 tests — 425 data tests and 26 unit
tests — and enforces a schema contract on all 17 marts. What each gate catches,
and the groups, exposures and model versions built around them, are in
[`docs/DATA_QUALITY.md`](./docs/DATA_QUALITY.md).

## Published dashboard

### 👉 [ddscully.github.io/dlt-dbt-duckdb-evidence](https://ddscully.github.io/dlt-dbt-duckdb-evidence/)

Ten pages, built from the modelled layers. No year is hardcoded: every page reads
the latest year each metric family can actually populate from
`sources/warehouse/latest_years.sql`, because coverage doesn't end in the same
year for all of them.

| Page | What's on it |
|------|--------------|
| **Home** | A routing page: pick the analysis that matches what you're responsible for. |
| **CBAM Exposure** | What a tonne of an imported CBAM good costs at the EU border, by where it was made: Annex I of Implementing Regulation (EU) 2025/2621, as corrected by (EU) 2026/1740, priced at a carbon price you choose. Semi-finished steel runs 63× from Azerbaijan to Indonesia, and the ranking sorts by *production route* rather than by the national grid, which is the opposite of the Scope 2 story. A screening tool, not a filing. |
| **Scope 2 Factors** | The same grid carbon-intensity series read as what it also is: the location-based Scope 2 emission factor a company multiplies its metered kWh by for a CSRD, SECR or CDP disclosure. `marts.dim_grid_emission_factors` as a reference table with its vintage and lineage, a worked example over twelve *invented* sites, and the three caveats a practitioner checks first. |
| **Retail Transactions** | One retailer's 1.07M invoice lines, the only page here below country grain. What counts as revenue when a negative quantity on a sale invoice is a stock write-off and not a return, cohort retention read as a triangle, what a customer's first order predicts about their lifetime value, RFM segmentation where SQL's `ntile` would split 3,227 customers away from their identical peers, and returns matched to their sale by inference. |
| **Currency** | The ECB's daily euro reference rates, and the three problems an annual warehouse never has to answer. 30% of calendar days carry no rate, so the daily table carries the last fixing forward, capped, because the two interior gaps in the series are the Icelandic króna after 2008 and the Argentine peso in 2002, not long weekends. Spot against average, and what it changes about a number already on the site: EU household electricity rose 35% or 13.5% from 2021-S1 to 2022-S2 depending only on whether you counted in euros or dollars. |
| **Eight Findings** | Eight write-ups on the joined data: when each country's emissions peaked, that the cleanup happened in electricity and coal is most of it, real-terms decoupling, whether it's just offshoring (it isn't, mostly), emissions tracking income rather than headcount, cumulative vs. current responsibility, carbon intensity falling while absolute tonnes rise, and the gap between the cleanest and dirtiest grids refusing to close. |
| **Country Explorer** | The same data with a year selector on it, for checking a specific country or year yourself instead of reading a conclusion. |
| **Coverage** | Which series actually cover which countries, by left-joining the fact onto the country-year spine so a gap is a row. Names both populations that break naive queries: territories with World Bank data and no OWID emissions, and countries with emissions and no World Bank GDP (Taiwan leads at 262 Mt, so it is silently absent from every intensity measure). |
| **Restatements** | Which CO₂ estimates OWID has revised since this warehouse first loaded them, off the dbt snapshot. |
| **Pipeline** | dlt load times per source, rows and year spans per layer, and all 425 dbt tests with their stored failure counts, from the observability tables that `transform/pipeline_status.py` writes. |

`.github/workflows/pages.yml` builds it as a single `publish_site` job. The site
is a node in the asset graph (`reports/evidence_site`), so the workflow
materializes it instead of running npm itself. It builds against the **live**
sources rather than the fixtures — a published dashboard showing the 17-country
test slice would be worse than none — weekly, on demand, and on any push to
`main` that touches something the site is built from. That last one is a
`paths:` allowlist rather than a `paths-ignore`, because `reports/pages/` is
markdown and ignoring markdown would stop republishing exactly when a page
changed.

Setting this up yourself takes three things nobody tells you about; they're in
[`reports/README.md`](./reports/README.md#deploying-to-github-pages).

## Published data

### 👉 [Latest snapshot](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/releases/latest)

The dashboard is one consumer of the warehouse. The warehouse itself is published
monthly, so you can use the joined data without running any of this: the whole
DuckDB file, a Parquet per modelled table, row counts and checksums. DuckDB will
query it over HTTPS where it sits, without downloading anything.

[`docs/PUBLISHED_DATA.md`](./docs/PUBLISHED_DATA.md) has the queries and the four
things worth knowing before you build on it.

## License

Code is [MIT](./LICENSE). The data is not this project's to license: OWID's
[CO₂](https://github.com/owid/co2-data) and
[energy](https://github.com/owid/energy-data) datasets are CC BY 4.0, World Bank
WDI is CC BY 4.0, Eurostat data carries its own
[reuse policy](https://ec.europa.eu/eurostat/help/copyright-notice), the
CBAM default values are EU law, reusable under
[Decision 2011/833/EU](https://eur-lex.europa.eu/eli/dec/2011/833/oj), the
euro reference rates are the ECB's, under its
[reuse policy](https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html),
[UCI's Online Retail II](https://archive.ics.uci.edu/dataset/502/online+retail+ii)
(Chen, D., 2019) is CC BY 4.0, and the daily capital-city weather comes from
[Open-Meteo](https://open-meteo.com/) under CC BY 4.0, generated using Copernicus
Climate Change Service information (ECMWF ERA5).

**Open-Meteo's data licence and its API terms are separate, and only one of them
restricts you.** The CC BY 4.0 above governs the numbers, so the release
redistributes them like every other source here. The *free API tier* is
additionally limited to non-commercial use and to 10,000 calls a day — that
binds this pipeline, which is why `raw.om_weather_daily` is paced and carried
forward between releases rather than refetched, and it does not follow anyone
who downloads the result.

**One source was deliberately left out on licence grounds.** Annexes II and III
of the CBAM regulation, the country electricity emission factors, are IEA data
under CC BY-NC-SA 4.0. Ingesting them would put a non-commercial and share-alike
restriction on a data release that is otherwise entirely permissive, so the
warehouse uses its own OWID-derived `dim_grid_emission_factors` instead and the
CBAM page says plainly that the two are not the same measurement.

All six permit redistribution with attribution, which is what the data releases
rely on. Every one ships an `ATTRIBUTION.md` naming the publisher and licence per
source. Attribute them, not this repo, for the numbers; the joins and derived
metrics are the only part that's ours. Nothing upstream is redistributed in the
repository *itself*: the pipeline fetches it at run time, and the checked-in
fixtures under `tests/fixtures/ingest/` are small excerpts kept for offline
testing.
