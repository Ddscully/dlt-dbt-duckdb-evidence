# Modern Data Stack Demo

A small, end-to-end demonstration of a **modern, lightweight data-engineering & BI stack** —
everything runs locally with `uv`, no cloud warehouse required.

```
dlt  ─▶  DuckDB  ─▶  dbt  ─▶  Polars  ─▶  Evidence
 EL      store     stg/marts   heavy T     BI-as-code
```

## Stack

| Tool | Role |
|------|------|
| [**uv**](https://docs.astral.sh/uv/) | project & environment manager |
| [**dlt**](https://dlthub.com/) | EL — API/CSV ingestion into DuckDB w/ schema inference |
| [**DuckDB**](https://duckdb.org/) | in-process analytical warehouse (a single file) |
| [**dbt**](https://docs.getdbt.com/) (`dbt-duckdb`) | T — staging + marts, tests, docs |
| [**Polars**](https://pola.rs/) | heavy columnar transforms / window logic in Python |
| [**Evidence**](https://evidence.dev/) | BI-as-code dashboard, deployable to GitHub Pages |
| [**marimo**](https://marimo.io/) | reactive notebook for exploration |
| [**Harlequin**](https://harlequin.sh/) | terminal SQL IDE for DuckDB |
| [**sqlfluff**](https://sqlfluff.com/) + pre-commit | SQL linting / CI rigor |
| **GitHub Actions** | run the pipeline + tests on every PR |

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

## Layout

```
.
├── ingest/            # dlt pipeline: sources -> DuckDB (schema `raw`)
├── dbt/               # dbt-duckdb project
│   ├── models/staging # 1:1 cleaned source views (stg_*)
│   ├── models/marts   # joined fact/dim tables (fct_*)
│   └── macros/        # generate_schema_name -> clean schema names
├── transform/         # Polars: derived metrics -> schema `analytics`
├── notebooks/         # marimo reactive notebooks
├── reports/           # Evidence dashboard (BI as code)
├── docs/sessions/     # exported Claude Code session logs (project history)
├── data/              # warehouse.duckdb lives here (gitignored)
├── justfile           # orchestration recipes
├── CLAUDE.md          # guidance for Claude Code / contributors
└── .github/workflows  # CI
```

## Quickstart

```bash
just setup      # uv sync runtime + dev + notebook groups
just run        # ingest -> dbt build -> polars transform
just sql        # poke around the warehouse in Harlequin
just notebook   # marimo exploration
just report     # build the Evidence dashboard
```

No `just`? The recipes map to plain commands — see the [`justfile`](./justfile).
