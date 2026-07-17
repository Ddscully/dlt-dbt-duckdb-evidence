# 2026-07-17 — Scaffold the modern data stack + Evidence dashboard

Session that bootstrapped this repo from an empty directory to a working,
verified pipeline with a BI dashboard.

## Goal

Build a public demo of a modern, lightweight data-engineering + BI stack using
at least dbt, DuckDB, and Polars, over interesting public data with joinable
tables (economic / social / environmental).

## Stack chosen

`dlt (EL) → DuckDB → dbt (staging/marts) → Polars (heavy T) → Evidence (BI)`,
plus marimo, Harlequin, sqlfluff + pre-commit, GitHub Actions, and `just` for
orchestration. Managed with `uv`.

## Data sources

All country + year keyed, freely licensed:

- OWID CO₂ & GHG — https://github.com/owid/co2-data
- OWID Energy — https://github.com/owid/energy-data
- World Bank WDI (GDP, life expectancy, population, poverty) —
  https://databank.worldbank.org/source/world-development-indicators
- World Bank countries (region + income group dimension) —
  https://api.worldbank.org/v2/country?format=json

## What was built (in order)

1. **Scaffold** — `uv init` package project; directory tree for ingest/dbt/
   transform/notebooks/reports; config for sqlfluff, pre-commit, CI; `justfile`;
   dbt models (staging `stg_*`, mart `fct_emissions_energy`); Polars transform;
   marimo notebook; READMEs.
2. **Verified the ingested schema** — ran the pipeline for real and inspected
   DuckDB (see gotchas below).
3. **Clean schema names + WDI source** — added `generate_schema_name` macro;
   added the World Bank WDI resource (5 indicators, fetched long, pivoted wide in
   `stg_wdi.sql`); joined WDI into the fact. Updated README, added `CLAUDE.md`,
   created `docs/sessions/`. Initialized git and made the first commit.
4. **Evidence dashboard** — `reports/` project reading the DuckDB warehouse;
   `pages/index.md` with a year selector, KPI tiles, renewables-vs-life-
   expectancy bubble chart, CO₂-intensity-by-income-group line chart, and a
   most-efficient-economies table. Built and verified.

## Gotchas discovered (the valuable part)

- **World Bank JSON is snake-cased by dlt**: API `iso2Code`/`capitalCity`/
  `incomeLevel.value` land as `iso2_code`/`capital_city`/`income_level__value`.
  My first `stg_country.sql` guessed wrong — verify against
  `information_schema.columns`.
- **Polars CSV type inference** samples only the first 100 rows. OWID's early
  rows (Afghanistan) are empty for most metrics, so 76/81 columns landed as
  VARCHAR. Fix: `pl.read_csv(..., infer_schema_length=None)`.
- **dlt persists its schema and only widens types.** Even after fixing Polars,
  columns stayed VARCHAR because dlt kept the first run's inferred text type.
  Fix: `pipeline.run(..., refresh="drop_sources")` to re-infer each run.
- **dbt-duckdb default schema naming** was `main_marts`/`main_staging`. Added a
  `generate_schema_name` macro to get clean `marts`/`staging`.
- **Evidence install** hit the known svelte2tsx peer-dependency conflict when
  using a trimmed `package.json`. Fix: adopt the official template's exact
  `package.json` + `package-lock.json` (renamed); `evidence.config.yaml` still
  enables only the DuckDB datasource.
- **Deploy ordering**: `data/warehouse.duckdb` is gitignored, so CI/Pages must
  run `just run` before `evidence build`.

## Verification

- Pipeline: `just run` → dlt ingest → `dbt build` (PASS=7: 5 models + 2 tests) →
  Polars transform (14,672 rows).
- Warehouse schemas: `raw`, `staging`, `marts`, `analytics`.
- WDI raw: 87,450 rows (5 indicators); `fct_emissions_energy`: 42,480 rows.
- Evidence: sources extracted 42,480 + 14,672 rows from the real warehouse;
  `evidence build` produced `build/` with queries' columns resolved and parquet
  shipped.

## Commits

- `8dc57e3` Initial commit: modern data stack demo (27 files, no data/binaries).
- `ecdf170` Add Evidence dashboard reading the DuckDB warehouse.

## Follow-ups

- Optional GitHub Actions workflow to run the pipeline and publish
  `reports/build/` to GitHub Pages (make the dashboard live).
- The poverty indicator (`SI.POV.DDAY`) is sparse — surveys are infrequent, so
  many country-years are null. Fine for a left-joined fact; note before building
  tiles on it.

---

_This is a structured summary written by Claude. For a verbatim transcript, run
`/export` in the Claude Code CLI and save it alongside this file._
