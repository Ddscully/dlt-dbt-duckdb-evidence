# Modern Data Stack Demo

A small, end-to-end demonstration of a **modern, lightweight data-engineering & BI stack** —
everything runs locally with `uv`, no cloud warehouse required.

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
├── scripts/           # record_fixtures.py — re-record those fixtures
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

CI on a pull request runs both, plus the Dagster asset graph and the asset
checks, entirely offline: `INGEST_FIXTURES=1` serves all five sources from
`tests/fixtures/ingest/`. A red build therefore means *this repo* broke, not that
OWID was rate-limiting. A separate nightly workflow runs the same graph against
the live endpoints and opens an issue when a source has moved — which is the cue
to fix the pipeline and `just record-fixtures`. Details in
[`tests/README.md`](./tests/README.md).
