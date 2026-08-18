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

Starting a *different* project on this shape is
[`docs/REUSING_THIS_STACK.md`](docs/REUSING_THIS_STACK.md): what carries over,
what has to be rewritten, and the decisions that are expensive to change later.
The rest of this file is about *this* warehouse.

**The README is the tour; the reference prose sits in `docs/`.** It was one
1,200-line file until 2026-08-10 and is now split by topic:
[`WAREHOUSE.md`](docs/WAREHOUSE.md) (sources, grains, schemas, the lake),
[`ORCHESTRATION.md`](docs/ORCHESTRATION.md) (the asset graph and its three jobs),
[`DATA_QUALITY.md`](docs/DATA_QUALITY.md) (tests, contracts, groups, exposures,
versions), [`PUBLISHED_DATA.md`](docs/PUBLISHED_DATA.md) (the release and how to
query it) and [`FOR_REVIEWERS.md`](docs/FOR_REVIEWERS.md). Those files carry the
*explanation*; this one carries what it cost to learn, and the two should not
start duplicating each other. A change to how a layer works usually needs an edit
in `docs/` **and** here.

## The package (`src/modern_data_stack/`)

The domain-neutral mechanisms live here — `paths`, `fixtures`, `lake`,
`observability`, `export`, `history` — and take their configuration as
arguments. The project modules that call them (`ingest/fixtures.py`,
`lake/archive.py`, `transform/pipeline_status.py`, `scripts/export_warehouse.py`,
`scripts/restore_history.py`) hold this project's constants and stay the entry
points, so `python -m lake.archive`, the justfile recipes and the asset keys are
all unchanged.

- **`modern_data_stack.paths` is the single answer to "where is the project".**
  It used to be `REPO_ROOT` in `ingest/pipeline.py`, defined as the parent of
  `ingest/` and imported from there by the lake, the observability tables, the
  exporter and the report builder — so every layer's sense of where it was
  depended on where the *ingestion* layer sat. Resolution is now `PROJECT_ROOT`,
  then the package's own grandparent when it looks like a project, then a
  marker search up from the cwd. The cwd comes last on purpose: the Dagster
  daemon and the CLI don't necessarily run from the project directory.
  **Exhausting all three raises**, and a cwd fallback must not be added back: it
  would resolve the warehouse to `./data/warehouse.duckdb`, which DuckDB then
  *creates*, so a non-editable install started outside the tree runs green
  against an empty database with nothing to read. `tests/test_paths.py` pins it.
- **Config reaches a package module as a parameter, never as a constant.**
  Nothing under `src/` knows what a country is, and that is the whole of the
  split — a hardcoded table name there undoes it.
- **`RawSchemaDltTranslator` stays in `orchestration/assets.py`** — twenty lines
  around two of that module's constants, and moving it would put Dagster (an
  optional dependency group) behind a package import.

## Commands

Use the `justfile` recipes (they map to plain `uv run …` commands):

| Command | What it does |
|---------|--------------|
| `just setup` | `uv sync --group dev --group notebook --group orchestration` |
| `just ingest` | run the dlt pipeline → `raw` schema in DuckDB |
| `just ingest-wdi-full` | same, ignoring WDI's incremental watermark (full re-fetch) |
| `just dbt-deps` | install dbt packages (`dbt_utils`) into `dbt/dbt_packages/` |
| `just dbt-build` | `dbt deps` then `dbt build` (26 models, 2 snapshots, 6 seeds + 368 tests) |
| `just dbt-freshness` | `dbt source freshness` — is the warehouse stale? |
| `just transform` | Polars derived metrics → `analytics` schema |
| `just pipeline-status` | load times, layer inventory, dbt test state → `analytics.pipeline_*` |
| `just lake` | year-partitioned Parquet archive of the warehouse → `data/lake/` |
| `just run` | ingest → dbt-build → transform → pipeline-status → lake (shell ordering) |
| `just dagster` | Dagster UI on :3000 — asset graph, runs, freshness, checks |
| `just materialize` | same pipeline, ordered by the asset graph (`load_retail` then `full_refresh`, no Evidence) |
| `just materialize-site` | the same two jobs + the Evidence site (`publish_site`; needs Node) |
| `just materialize-select 'raw/wb_wdi*'` | one asset + everything downstream (`*` all, `+` one layer) |
| `just backfill-wdi 1990 1995` | re-load WDI for one year or a range — the partitioned `raw/wb_wdi` asset |
| `just report` / `just report-clean` | build the Evidence site (`--clean` drops the schema cache) |
| `just export-data` | package `data/export/` — the DuckDB copy + Parquet + checksums that `release-data.yml` publishes |
| `just restore-history prev/warehouse.duckdb` | copy `history` out of a published release so `dbt build` appends to that snapshot |
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
`just lint` (pre-commit runs the same check — literally: the hook is a `local`
one whose entry is `just lint`).

- **sqlfluff is pinned exactly** (`sqlfluff==4.2.2` in `pyproject.toml`) and lives
  in exactly one place. Don't restore the upstream `sqlfluff/sqlfluff` pre-commit
  hook: it installs its own copy, which is how the repo ended up with 3.3.0
  rejecting an `order by` inside a window clause that the venv's 4.2.2 accepts —
  `just lint` passed and the commit hook failed on the same file. It also can't
  run from `dbt/`, so the dbt templater resolves `profiles.yml`'s
  `../data/warehouse.duckdb` one directory too high and dies before linting.
- CI lints via the same venv, so it agrees on rules, and over the same paths
  (`models snapshots`) — a narrower set there would mean CI passing SQL a
  contributor's commit hook rejects.
- **The `just lint` hook means CI has to install `just`.** `ci.yml` runs
  `pre-commit run --all-files`, and a `local` hook whose entry is a recipe fails
  with "Executable `just` not found" on a runner that hasn't got it — which is how
  the hook shipped green locally and red on the first push (`uv tool install
  rust-just` + `$GITHUB_PATH` is the fix). Anything else moved into a `local` hook
  inherits the same requirement.

The Python half is ruff, configured in `pyproject.toml` and run only through
pre-commit (`ruff-check` with `--fix`, then `ruff-format`).

- **ruff runs its own default rule set, and that set is not stable across
  versions.** 0.9 enabled 59 rules; 0.16 enables 413 across 40 families. There is
  deliberately no `select` — what holds the rules still is the exact `rev` in
  `.pre-commit-config.yaml`, so `pre-commit autoupdate` is the only thing that can
  change what is enforced. This is the mirror image of sqlfluff: ruff is *not* in
  the `dev` group, so pre-commit's copy is the only one and can't drift.
- **`extend-select` re-adds the 18 rules 0.16 dropped** from the defaults
  (`E401`, `E402`, the `E7xx` comparison rules, `F403`/`F405`/`F406`, `F722`).
  They were enforced before the widening and the tree still passes them; without
  the list, a version bump silently stops checking star imports and `== None`.
- **`combine-as-imports = true`, or the import blocks get shredded.** ruff's
  default splits `from x import a, run as b` onto two statements. Four layers here
  each export a `run()`, so `orchestration/assets.py` aliases every one of them —
  the default turns its eight-line import block into twelve and separates
  `run as write_lake` from its module.
- **`B018` is ignored for `notebooks/*.py` on purpose.** marimo stores a notebook
  as Python where each cell is a function, and the bare expression on a cell's
  last line is how it renders — `df` at the end of a cell *is* the chart. The rule
  is right about the syntax and wrong about the file; taking its fix blanks the
  notebook.
- **The hook id is `ruff-check`.** Plain `ruff` still works but is the legacy
  alias as of 0.12.
- **0.16 formats Python code blocks inside Markdown by default.** The hook is
  scoped to `types_or: [python, pyi, jupyter]` so it never sees `.md` and CI is
  unaffected — but a manual `ruff format .` will rewrite python blocks in `docs/`
  and `README.md`. Evidence pages use `` ```sql `` blocks and are untouched.

## Dependency and action versions

`.github/dependabot.yml` watches three ecosystems — `github-actions` (`/`), `uv`
(`/`) and `npm` (`/reports`) — monthly, each grouped to a single PR.

- **It exists because green CI proves nothing about versions.** Every action sat
  on a Node 20 major for months while `ci.yml` passed, until the runners started
  warning that they were forcing those actions onto Node 24. Nothing in the repo
  could have said so.
- **The `uv` entry is `versioning-strategy: lockfile-only`, and must stay that
  way.** The bounds in `pyproject.toml` are minimum-supported versions, not pins
  (`duckdb>=1.1`, `polars>=1.17` sit far below what resolves). Dependabot's
  default for uv raises the lower bound instead, and its first run did exactly
  that — `dlt[duckdb]>=1.5` to `>=1.29.1`, dropping two dozen releases for no
  change to what installs, since `uv.lock` already resolved there.
- **`npm` is deliberately left on the default.** Those are `^` ranges, so the
  major *is* the pin and an Evidence 40 → 41 bump is a real upgrade worth seeing
  as a PR. `lockfile-only` there would quietly cap the site at 40.x forever.
- **Dependabot scans the moment the config lands**, not on the next scheduled
  date — expect PRs immediately after touching that file.
- **A yanked release stays locked until something re-resolves.** `uv.lock` held
  `polars==1.43.1` after upstream yanked both it and 1.43.0; nothing said so
  until an unrelated `uv lock` printed the warning in passing. Installs of a
  yanked version keep working — that is the point of a lockfile — so the monthly
  Dependabot PR is the only thing that would have cleared it, up to a month
  later. `uv lock --upgrade-package <name>` is the targeted fix and leaves the
  other 196 packages alone. Worth reading the warnings on any re-lock, since
  that is the one moment they appear.
- **`astral-sh/setup-uv` is pinned to an exact patch, not a major.** It stopped
  publishing moving major/minor tags at v8 as a supply-chain measure, so `@v9`
  does not resolve at all. All four workflows carry a comment saying so, because
  the obvious tidy-up is to "simplify" it back to a major.
- **`pages.yml` is the only workflow that needs Node** (24; the Evidence build).
  The other three run on a bare uv checkout — see the Orchestration section for
  why the site is excluded from `full_refresh`.
- **Python is 3.13, set in one place: `.python-version`.** No workflow passes a
  `python-version` to `setup-uv`, so that file is what CI, the release job and a
  contributor's venv all read. Nothing watches it — Dependabot covers
  `github-actions`, `uv` and `npm`, none of which see it, so the interpreter is
  the one version here that can only age deliberately. It sat on 3.12 from the
  initial commit to 2026-08-10 for no reason anyone recorded.
- **`requires-python` tracks it (`>=3.13`), and here that bound is *not* a
  minimum-supported floor** — the opposite of the dependency bounds above, so
  the exception is worth knowing. This is an application, not a library: it
  ships a committed `uv.lock` and a `.python-version`, and nothing installs it
  as a dependency. A floor below what CI builds is a promise no job tests. It
  was briefly `>=3.12` against a 3.13 `.python-version` and that is the state to
  avoid — CI reads `.python-version`, so 3.12 was claimed and never exercised.
- **`requires-python` is what `uv.lock` resolves against, so it is not
  cosmetic.** Raising it to `>=3.13` dropped a package (`win-precise-time`) and
  ~400 lines of `python_full_version < '3.13'` marker branches. Lowering it
  again is a re-lock, not an edit.
- **Ruff's target version is inferred from `requires-python`**, with no
  `target-version` in `[tool.ruff]` — so that one line also decides which
  rewrites the linter will make. It reports `3.13` now; it reported `3.12`
  before, which is why nothing 3.13-only could have been written in the gap
  even by accident.
- **3.14 is blocked on dbt, not on us.** `uv lock --python 3.14` resolves, but
  that only proves the solver is happy — dbt-core 1.10 ships no 3.14 classifier,
  and dbt Labs certifies a Python roughly a year behind. `dagster<3.15` is the
  only hard upper bound in the tree.

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
  `eu_elec_prices`, `ecb_fx_rates`, `retail_invoice_lines`
- `staging` — dbt views, `stg_*`, cleaned to `(country_iso3, year)` grain —
  except `stg_fx_rates`, which is `(rate_date, quote_currency)`, and
  `stg_retail_lines`, which is `(invoice, line_number)`
- `marts` — dbt tables: `dim_country_year` (the country-year spine),
  `fct_emissions_energy` (the wide join, built on the spine, and **the one
  versioned model** — `fct_emissions_energy_v1` is a compatibility view live
  until 2026-11-01),
  `dim_grid_emission_factors` (the Scope 2 reference product),
  `fct_co2_estimate_versions` (revision history, off the snapshot),
  `fct_eu_electricity_prices_semiannual` (Eurostat's own half-year grain),
  `fct_example_scope2_emissions` (the worked example — **the only fabricated
  data in the warehouse**), `fct_cbam_exposure` (the CBAM border cost, at
  `(sourcing country, good)` and **no year at all**), and the five with no
  country in them at all: `dim_date` (the calendar), `dim_currency`,
  `fct_fx_rates_published` (the ECB's fixings as published, and **the project's
  only incremental model**), `fct_fx_rates_daily` (gap-filled) and
  `fct_fx_rates_periods` (month / quarter / half / year). Plus the five retail
  models, the only ones at a grain below a country: `fct_retail_order_line`
  (`(invoice, line_number)` — the warehouse's finest grain), `dim_retail_product`,
  `dim_retail_customer`, `fct_retail_returns` and `fct_retail_customer_cohorts`
  (`(cohort_month, months_since_first_order)`)
- `history` — the dbt snapshots `snap_co2_estimates` (SCD2 versions of OWID's CO2
  numbers) and `snap_grid_emission_factors` (the same for the Scope 2 factors,
  2015+). **The two tables here that no rebuild can reproduce** — see below
- `analytics` — Polars output: `co2_intensity` and `retail_rfm`, plus
  `pipeline_sources` / `pipeline_tables` / `pipeline_tests` (see *Pipeline
  observability* below)

Grain of every *country* fact/staging model is **`(country_iso3, year)`**; joins
are on ISO3 country code + year. The country dimension (`stg_country`) supplies
`region` and `income_group`. Two of those models are Eurostat prices at their
published `(country_iso3, year, half)` grain —
`stg_eu_electricity_prices_semiannual` and the mart off it — and they are the
exception on purpose, not a model waiting to be flattened; see the Eurostat bullet
under *Conventions & gotchas*.

**That sentence used to say "every model", and it stopped being true twice.**
`fct_cbam_exposure` has no year (a regulatory schedule, not a time series) and
the FX tables have no country. The country-year spine is the *dominant* grain
here, not a house rule — reaching for `dim_country_year` when the thing being
modelled isn't a country-year is how you get a fact with a fabricated dimension
on it.

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
- **The published history is carried, not rebuilt** (`scripts/restore_history.py`,
  `just restore-history`). Every workflow builds from an empty file, so the
  release and the site used to hold one version per row forever.
  `release-data.yml` now downloads the previous `data-*` release and copies its
  `history` schema in *before* the graph runs, so `dbt snapshot` compares this
  month's numbers against last month's; `pages.yml` borrows the same file so the
  Restatements page shows real revisions. Details in *Publishing* below.
- **CI still starts from an empty file**, so there every row is version 1 and
  `is_revised` is uniformly false. The restatements page renders an explicit
  "nothing revised yet" branch for that case; it is the honest state, not a
  broken build.
- **There are two snapshots now, and anything that names one must name both.**
  `snap_grid_emission_factors` (the Scope 2 section below) was the second, and it
  found the places that had hardcoded the first: `release-data.yml` counted
  restored rows and asserted "history didn't shrink" against
  `history.snap_co2_estimates` by name, so a snapshot added later would have been
  carried forward by `restore_history` but never verified. Both spots now sum
  over every table in the schema. `scripts/restore_history.py` needed no change —
  it copies the schema, not a table list — which is the reason to keep it that
  way.
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

## Scope 2 emission factors (`dim_grid_emission_factors`, `reports/pages/scope2.md`)

`carbon_intensity_elec_g_kwh` is already in the wide fact. It is modelled a
second time as `marts.dim_grid_emission_factors` because under the GHG Protocol
that series **is** the location-based Scope 2 emission factor — the number a
multi-site company multiplies its metered kWh by for the electricity line of a
CSRD / SECR / CDP disclosure. No new ingestion, no new analysis: the work was
packaging.

- **It is a product table, so the columns beside the factor carry the weight.**
  The factor in both units (`g_co2_per_kwh` as published, `t_co2_per_mwh` as
  meter data arrives — shipping only the first is how a filing gains a factor of
  1000), the vintage, and the lineage (`factor_basis`, `source_dataset`,
  `source_loaded_at`). The three constant-per-row columns are deliberate: the
  table ships as a standalone Parquet in the data release, and a factor detached
  from its basis is the one thing a reporter must not be handed.
- **`is_latest_available` is a filter, not a year, and that is the whole vintage
  problem.** "The most recent published factor for country X" resolves to 2025
  for 90 countries, 2024 for 105, 2023 for 10 and 2022 for 2, so a
  `where year = 2025` cross-section drops more than half the world. Same lesson
  as `latest_years.sql`, load-bearing here for a number with a legal
  consequence. A `unique_combination_of_columns` test with
  `config: {where: "is_latest_available"}` is what holds it to one row per
  country. Grid size is no protection: Ukraine's newest factor is 2022, on a
  111 TWh grid.
- **Not built on the spine, unlike every other model here.** A country-year with
  no factor is an absence, not a reference value; `dim_country_year` is where
  absences are rows. The dimension is still authoritative for what a country is,
  so five OWID territories (Guadeloupe, Martinique, Réunion, French Guiana,
  Falklands) carry no factor — the page says so rather than leaving the count
  unexplained.
- **`source_loaded_at` is why `stg_energy` now selects `_dlt_load_id`.** Same
  expression `dbt source freshness` uses. It answers "which extract did this
  number come out of", which is the assurance question, and it is the only
  reason that column exists in staging.
- **`fct_example_scope2_emissions` is invented and must stay obviously invented.**
  Twelve hypothetical sites (`seeds/example_scope2_sites.csv`) x real factors:
  582.5 GWh, 232,456 tCO2e, and the two cleanest-grid plants drawing 17% of the
  power for 1.7% of the tonnes. It is the only fabricated data in the warehouse
  and it *ships in the public data release*, so the "example" in both names, the
  seed description, the mart description, the `<Alert status=warning>` above the
  table and the release notes bullet are all load-bearing. Don't quietly rename
  it to something that reads as real.
- **The seed's countries must come from the fixture slice**, i.e. `COUNTRIES` in
  `scripts/record_fixtures.py`. The `not_null` tests on the factor join are real
  gates, and CI builds against the 17-country fixtures — the first draft was a
  twelve-site *European* group, which passed locally and failed `dbt build` with
  8 null factors under `INGEST_FIXTURES=1`, because only six of the fixture
  countries are European. This is the usual fixture-slice trap (CLAUDE.md's
  "17 countries will happily pass a threshold the full 200+ would break") running
  the other way: the slice is too *narrow* for a seed that joins to it. The
  global footprint is the fix and the spread is better for it.
- **The three caveats are stated on the page, not hidden.** Location-based only
  (market-based needs RECs/GOs, which no public dataset carries), an annual
  average rather than hourly matching, and production- rather than
  consumption-based. Naming them is the difference between a credible reference
  table and a liability; a practitioner checks all three first.
- The page quotes a 57x spread across grids above 10 TWh where `findings.md`
  quotes 24x above 150 TWh. Both are correct and the page says why — if one
  moves, check the other.

## CBAM exposure (`fct_cbam_exposure`, the `cbam_*` seeds, `reports/pages/cbam.md`)

Annex I of Implementing Regulation (EU) 2025/2621, as corrected by (EU) 2026/1740
— the country x good default values an importer uses from 2026 when they have no
verified supplier data — transcribed into two seeds, priced with a third, and
multiplied by a carbon price. 11,665 rows over 121 countries and 260 goods. The
only model here with **no year in its grain**: it is a regulatory schedule, not a
time series.

- **A seed, not a dlt resource, and that is the interesting decision.**
  Regulatory reference data is versioned by *amendment*, not by scrape; there is
  no API, and the values change when a new implementing regulation says so.
  `scripts/build_cbam_seeds.py` regenerates both seeds from the Commission's
  published workbook, so the next amendment is a re-run and a reviewable diff
  rather than a re-transcription. `country_overrides.csv` is the precedent.
- **Two seeds because normalising the goods out is worth 1.6 MB.** 12,540 value
  rows share 283 (product group, CN code, description) triples and one
  description runs to 250 characters. `cbam_goods` is 60 kB; inlined it would be
  1.6 MB of CSV and the same again in the warehouse.
- **A CN code is not a key** — it was flatly true and is now only mostly true.
  `2523 10 00` was both white clinker and grey clinker, whose values differ by
  more than 2x, so the grain is (CN code, description) and `good_key` is a slug
  of the pair. The 2026/1740 correction gives those two 10-digit **TARIC** codes
  (`2523 10 00 10` / `2523 10 00 90`) and closes that particular case, but the
  annex still prints 4- and 6-digit headings above the rows carrying the numbers,
  so the composite key stays. It is also what kept those two rows apart for the
  six months the codes could not, which is the argument for not renumbering to a
  surrogate.
- **The transcription is faithful, defects included, and the mart is where they
  are handled.** The annex is a legal instrument; cleaning it in the seed would
  put this project's judgement between the regulation and a euro figure. **Three
  of the four documented quirks were fixed by the 2026/1740 correction** — which
  is the vindication of the policy, not a reason to drop it: the body that wrote
  the instrument corrected it, and this project would have baked its guesses in.
  Kept here because the *handling* is still the reason parts of the mart look the
  way they do:
  - Albania's white Portland cement used to be published with `-` for direct,
    indirect and total and its three values sitting in the *mark-up* columns
    instead. Clean `-` now.
  - Five cement rows (Angola, Argentina) used to **compound** the mark-up —
    x1.1, x1.21, x1.331 — where the other 10,926 added it. With no published
    mark-up column there is nothing left to compound, and
    `markup_schedule_is_irregular` went with it.
  - Chile's line pipe had a total and a blank 2026 cell. Gone too, but it is
    what proved the fallback is a **row-level rule, not a column-level one**: a
    per-column `coalesce` paired Chile's tonnage with the *fallback's* mark-up
    and produced a 100% implied rate — a row that exists nowhere in the
    regulation. The rule outlives the row; direct/indirect/total still have to be
    read off one source.
  - 23 of the goods carry no value in any country, including the fallback. They
    are 4-digit CN *headings* whose subheadings hold the numbers, and they are
    excluded from the mart — rows that could only be priced at null. **This one
    survives**, and it is where `see below` lives (below).
- **Fertilisers carry a 1% mark-up in all three years**, not 10/20/30% and not
  1/2/3%, so the mark-up is a property of the product group — hardcoding one rate
  overstates every fertiliser line by nine points in 2026 and twenty-seven in
  2028. **The mart used to derive this and now asserts it**, which is a real loss
  and not a refactor. The annex published each good's marked-up value for each
  year, so `mode()` over published/total read the schedule off the data and an
  amendment moving a rate needed no edit. 2026/1740 publishes only direct,
  indirect and total. The schedule is the `cbam_markup_schedule` seed now —
  a seed and not a var or a `case`, so the carve-out stays reviewable as data —
  and it is confirmed against both the articles and the February annex's own
  columns, where all 10,929 priced rows imply exactly those rates. The stated
  rates are actually *cleaner* than the published ones: those carried rounding
  noise from the OJ's three decimals, so some rows implied 9,9% or 1,1%.
  - **What replaced the mark-up tests is `direct + indirect = total`** — the only
    internal consistency the corrected source still offers, and it reaches
    **2,781 of the 12,540 rows**, which is the part worth knowing before trusting
    it. `indirect` is published only for cement, fertilisers and 34 iron-and-steel
    rows; 8,129 rows carry direct and total with no indirect and nothing checks
    them. Tolerance 0.02, measured rather than generous: the annex rounds each
    column *independently*, so 711 of those 2,781 are inexact by 0.001–0.01 with
    nothing wrong (Albania's nitric acid is 2,73 + 0,04 = 2,76). It still catches
    the failure that matters, a column read from the wrong position, which is off
    by whole units.
- **Round half up, not `round()`.** The OJ prints three decimals and the
  Commission's XLSX mark-up cells are live formulas, so they arrive as binary
  floats. Python's banker's rounding turns 7,7165 into 7.716 where the regulation
  says 7,717. Eleven rows across the seven spot-checked countries differed in the
  third decimal before `Decimal` + `ROUND_HALF_UP`. Small, but the column is
  multiplied by a carbon price and shown as money.
- **An unreadable cell raises; it must never become a `None`.** `_number` used to
  `return None` on any `ValueError`, and `None` is not an error downstream — it
  is the annex's own "no value here", which `fct_cbam_exposure` reads as *use the
  fallback row* and prices. So a cell the parser merely failed to understand
  would not surface as a gap but as a plausible euro figure attributed to a
  country the regulation never assigned it to. `NO_VALUE` is now the accepted
  blanks and anything else stops the script. Checked against all 37,620 value
  cells of the real workbook: **one** token was reaching that catch-all —
  `see below`, on the 4-digit CN headings 3102 and 3105 whose numbers live in
  the subheadings under them (2,610 cells). Its null was *right*, and arrived by
  luck; it is in `NO_VALUE_PHRASES` now, so it is a decision. This is the same
  argument as the Chile row above — the fallback is a rule the annex states, not
  a landing zone for whatever didn't parse.
- **The seeds are Annex I as corrected by IR 2026/1740** (adopted 20 July 2026,
  in force 3 August, applying retroactively from 1 January 2026), which replaced
  Annexes I and IV in full. Migrated 2026-08-18. The amendment path paid for
  itself — re-running `build_cbam_seeds.py` was most of the work — but four
  things about it are worth keeping, because none are visible in the values:
  - **The Commission republishes at the same URL.** There is no versioned link;
    the workbook's `Version History` sheet is the only thing that says which
    amendment you are holding, and the `?filename=…v20260204…` query parameter in
    `ANNEX_XLSX_URL` is *stale and cosmetic* — the document id is what resolves,
    and it served v2 under a v1 filename. Check that sheet, not the URL.
  - **It failed loudly, which was luck rather than design.** The layout went from
    9 columns to 6, so `_route(row[8])` raised `IndexError` on the first sheet.
    Had the correction *added* a column instead, every field would have shifted
    one right and parsed fine into the wrong meaning. `COLUMNS` names the
    positions now and `parse_annex` refuses a row that isn't the expected width.
  - **`SHEET_TO_ISO3` being exhaustive is what caught the relabelling.** Ten
    countries were renamed to ISO-style long forms with no change of country
    ("Russia" → "Russian Federation", "Côte d'Ivoire" → "Ivory Coast") and two
    are new (Liberia, New Caledonia). The script stopped and named all twelve
    rather than writing blank ISO3 codes and dropping them at the mart's join.
  - **The values barely moved**: 66 of 10,503 comparable rows, 38 of them down by
    2% or less, 28 now blank. Everything expensive about the migration was
    structural.
- **`Annex IV` is a new sheet and is deliberately not transcribed.** It is the
  single *highest* default value per good, with no country dimension — a
  different table answering a different question, and the circumstances in which
  a declarant must use it instead of the country value are set by the articles,
  not the annex. Those articles could not be confirmed from a primary source
  (EUR-Lex does not serve to the fetcher), and inventing a legal trigger is
  exactly what the rest of this layer refuses to do. In `SKIP_SHEETS` with that
  reason written next to it. Same posture as Annexes II and III, different
  ground: those are excluded on licence, this one on not knowing.
- **Dropping a seed column needs `dbt seed --full-refresh`.** dbt-duckdb derives
  the CSV's column spec from the *existing* relation, so removing the three
  mark-up columns failed with a sniffer error naming 10 columns against a file
  that plainly had 7 — and `--no-partial-parse` does not help, because the stale
  shape is in the warehouse and not in the manifest.
- **The ETS price is a dbt var (`eu_ets_price_eur_per_t`, EUR 75), not a
  measurement.** There is no clean free API for EUA spot. The mart ships the
  tonnage columns beside the euro columns and states the price per row, so the
  page draws its EUR 60-120 sensitivity from one build and a release consumer can
  re-price without rebuilding.
- **Annexes II and III are deliberately not ingested.** They are the country
  electricity emission factors, and they are IEA data under **CC BY-NC-SA 4.0** —
  redistributing them would put a non-commercial and share-alike restriction on a
  data release that is otherwise entirely CC BY 4.0. `dim_grid_emission_factors`
  (OWID) sits beside the annex's numbers as context and **is not the same
  measurement**; the page says so. This is also why the mart carries only the grid
  factor and no derived reconciliation against the annex's indirect column.
- **The story is production route, not grid** — the opposite of the Scope 2 page.
  Semi-finished steel runs 63x from Azerbaijan to Indonesia, and sorting by cost
  sorts almost perfectly by the annex's route indicator (`E` scrap/EAF against
  `C`/`F` ore/BF-BOF), not by the country's grid — the correlation between a
  country's steel default and its grid factor is 0.32 (it was 0.26 before the
  2026/1740 correction; the spread held at 63x through it).
- **Excel mangles the country names, so `country_display_name` exists.** Sheet
  names cap at 31 characters and forbid some punctuation, which is why the annex's
  Koreas arrive as `North Korea (Democratic People’` and
  `Korea, Republic of (South Korea`, both cut mid-parenthesis. The seed keeps the
  annex's label because it is the legally meaningful one; the mart coalesces to
  `stg_country.country_name` for anything that goes on a chart. **Which names are
  mangled moves with the amendment** — before 2026/1740 the pair to quote were
  `Democratic Republic of the Cong` and `Myanmar_Burma`, and both of those are
  clean now while two others became truncated. That is the argument for
  coalescing to the dimension rather than patching labels one at a time.

## Currency and the calendar (`dim_date`, the `fx_*` models, `reports/pages/currency.md`)

The ECB's daily euro reference rates, via [Frankfurter](https://frankfurter.dev)
— no key, no quota, the whole 1999-2026 series in one 3.6 MB request. It is the
**first sub-annual grain in the warehouse**, and everything interesting about it
follows from that rather than from the numbers.

- **The source is small on purpose.** The modelling is the point: a date
  dimension, a gap-filled daily series, a spot-vs-average decision and the first
  incremental model. A harder API would have bought nothing.
- **30% of calendar days carry no rate**, and that is the model. 7,066 of 10,078
  days have a fixing; the rest are 2,878 weekend days and 134 weekday TARGET
  closures. `fct_fx_rates_daily` carries the last fixing forward, which is what a
  finance system does and is the same operation as a slowly-changing lookup —
  and `rate_source_date` says which fixing every row is quoting.
- **The carry-forward is capped at 7 days (`fx_max_carry_forward_days`), and the
  bound is measured.** The longest closure the ECB has ever taken is 5 days
  (36 times, the Christmas/New Year runs). What the cap refuses is the two
  *interior* gaps in the whole series, and both are currency crises rather than
  calendars: the Icelandic krona has no rate for 3,341 days from the 2008 banking
  collapse to February 2018, the Argentine peso none for 34 days from the January
  2002 breaking of the dollar peg. Those 3,359 rows keep their place with a null
  rate and `is_rate_stale` set. An uncapped fill would have put a pre-collapse
  krona on nine years of charts.
- **The currency panel is not fixed, which is why `dim_currency` exists.** 46
  codes have been quoted and 29 still are. Ten stop on the last business day
  before their country adopted the euro (GRD 2000 through BGN 2025), two at a
  redenomination where the money continues under a new code (TRL→TRY at
  1,000,000:1, ROL→RON at 10,000:1 — a chart following the *code* has a cliff in
  2005), and five simply cease. **The `currencies` seed carries the twelve dates
  that are matters of public record and deliberately guesses at none of the
  other five**, and a test checks each one against the series: every asserted
  retirement date is the day after the last published fixing. `is_quoted` is
  false for exactly one row — EUR, which is the base of every quote and never a
  quote itself.
  - **A hand-maintained seed needs a test in *both* directions, and only one of
    them existed.** `dim_currency` is `from seed left join` the series, so it can
    only ever hold seed rows — and `fct_fx_rates_daily` inner-joins it. A
    currency the ECB starts quoting that nobody adds to the seed therefore
    vanishes from the dense table while still appearing in
    `fct_fx_rates_published` and `fct_fx_rates_periods`: a silent per-currency
    hole, not a build failure, and the shape that makes it hard to spot is that
    only *one* of the three tables is wrong. A `relationships` test on
    `stg_fx_rates.quote_currency` closes it. The reverse — a seed row the series
    never quoted — could not be caught by the `retired_on` test either: its
    subquery returns NULL for a code with no rates, and `retired_on > NULL` is
    null rather than false, so the row passed by being *unknown*. Hence the
    `is_quoted` assertion: 47 rows, 46 quoted, EUR the one exception.
- **Both directions of every rate ship.** `units_per_eur` is the ECB's own quote,
  `eur_per_unit` its reciprocal. Same argument as the Scope 2 factor in g/kWh
  *and* t/MWh: a consumer forced to invert it themselves will eventually forget.
- **Spot or average is a real decision and the model refuses to make it.** Stocks
  (a balance at an instant) convert at the closing rate; flows (revenue, spend, a
  price over a period) at the period average. `period_end_vs_avg_pct` measures
  the cost of choosing wrong — +11.7% for EUR/USD in 2003, -8.6% in 2014, +98%
  for the krona in 2008.
  - **Average over published fixings, never over the dense table.** Averaging
    `fct_fx_rates_daily` counts every Friday three times and four or five times
    around a holiday, weighting the mean toward whichever weekday sits next to a
    closure. `fct_fx_rates_periods` reads `fct_fx_rates_published` for that
    reason alone.
  - **`avg_eur_per_unit` is not `1 / avg_units_per_eur`** — the mean of
    reciprocals is not the reciprocal of the mean. 0.07% apart in a calm year,
    0.53% in 2008. Each column is the mean of its own series; the period-*end*
    columns invert exactly, because a single point has no averaging in it.
- **`dim_date` is a calendar and not a market calendar.** It knows weekends; it
  does not know trading days in any jurisdiction, and the TARGET closures are
  observed as absences rather than asserted from a list that would need
  maintaining forever. Two traps it exists to stop:
  - **ISO year is not calendar year.** 2021-01-01 is a Friday in ISO week 53 of
    ISO year *2020*, and 2019-12-30 is already in week 1 of 2020. Grouping by
    `(year, iso_week)` splits one week over two buckets. Pair `iso_year` with
    `iso_week`, or group on `iso_week_start_date`.
  - **`/` is float division in DuckDB, and `cast(... as integer)` rounds.** The
    fiscal-quarter expression `((month - start + 12) % 12) / 3 + 1` gives 4.67
    for March under an April year start, which cast to an integer is **quarter
    5**. `floor()` is the fix, and the test that caught it is an
    `accepted_range` of 1-4.
  - The fiscal columns come from the `fiscal_year_start_month` var (4 = April)
    and **the value used is carried on every row**, because the same Tuesday is
    in a different fiscal year on someone else's books. `fiscal_year` is the year
    the fiscal year *ends* in, which is what makes it collapse onto `year` when
    the var is 1.
- **`fct_fx_rates_published` is the only `materialized='incremental'` model in
  the project, and it is the right one rather than the biggest one.** Every other
  table here re-derives a source that gets fully re-fetched, so rebuilding it is
  how a restatement is picked up, not waste. This one grows ~30 rows a day
  forever and a published fixing never changes. Numbers, honestly: incremental
  0.16 s against 0.24 s full-refresh at 265k rows — the saving is 0.08 s and the
  argument is the shape of the curve, not today's seconds.
  - **`delete+insert` on the grain, not `append`.** Ingestion re-asks for a
    lookback window and an append would duplicate every row in it. Same
    idempotence argument as `wb_wdi`'s merge key one layer up.
  - **Two lookback windows, deliberately not equal.**
    `fx_incremental_lookback_days` (30) must be **no smaller** than
    `FX_LOOKBACK_DAYS` (10) in `ingest/pipeline.py`. Two constants that have to
    match are a drift bug; one that only has to be no smaller costs a few rows a
    run and cannot fail in the direction that loses data.
  - A `dbt_utils.equal_rowcount` against `stg_fx_rates` is the guard. It only
    works because this model is a faithful copy of the view — keep it that way,
    or that one cheap test stops meaning anything.
- **The FX watermark is one value for the table, where WDI's is one per
  indicator.** Every currency comes back in the *same* request, so a newly listed
  one is covered by the table-wide high-water mark. WDI needs the per-indicator
  form precisely because adding an indicator adds a request that has never been
  made before. Same mechanism, opposite answer, for a reason worth keeping
  straight.
- **The FX fixture is the whole series, gzipped** (3.6 MB → 843 kB), and it is
  the one fixture that isn't trimmed. Every discontinuity above is something a
  model is tested against, so cutting the date range would take the euro
  changeovers, the rouble and Iceland out of CI. It is also the reason `_get_json`
  has a `.gz` branch.
- **The one thing it changes about a number already on the site.** Eurostat's
  household electricity price is the warehouse's only euro-denominated
  measurement, sitting beside the World Bank's dollar GDP. Converted at the
  half-year average, the 39 countries present in both halves rose **35%** from
  2021-S1 to 2022-S2 in euros and **13.5%** in dollars, because the euro fell
  from 1.205 to 1.014 over the same eighteen months. Neither is wrong; a chart of
  "European electricity prices" with no stated currency is reporting the exchange
  rate as if it were an energy market. That is the `gdp_usd` vs
  `gdp_constant_usd` gotcha below, finally measured instead of narrated.
- **`marts.fct_fx_rates_daily` is archived to the lake and `raw.ecb_fx_rates` is
  not** — the reverse of every other table there. The landing table is keyed on
  `rate_date` and has no `year` to partition on. It is also the only table that
  improves the archive's small-file arithmetic: 381k rows over 28 partitions.

## Retail transactions (the `retail_*` models, `analytics.retail_rfm`, `reports/pages/retail.md`)

[UCI Online Retail II](https://archive.ics.uci.edu/dataset/502/online+retail+ii)
— a UK gift wholesaler's complete transaction log, 1,067,371 lines over
2009-12-01 to 2011-12-09. **The first grain below a country**, the first source
that is a bulk file drop rather than an API, and the first fact recording what a
person did rather than what an agency published. Six dbt models plus a Polars
one; the page is `retail.md`.

- **The mess is the deliverable.** Nothing about this source has been cleaned by
  anyone, so the modelling *is* the value — and each decision has a wrong answer
  that produces a plausible number. The three that matter, all measured:
  - **A negative quantity is not a return.** 3,457 negative lines sit on *sale*
    invoices, every one priced at exactly zero with no customer: inventory
    write-offs (damage, stock counts, one row labelled `check`). Reading them as
    returns inflates the return count by a fifth and the returned value by
    nothing — an error that survives review because the money still balances.
    `is_stock_write_off` and a test hold it.
  - **There are three invoice prefixes, not two.** Beside the `C` cancellations
    sit six `A` bad-debt adjustments worth −£147,614, and they are the only
    negative *prices* in the file.
  - **A returned quantity is positive everywhere, and for a while it wasn't.**
    `fct_retail_returns` negates the source's sign on purpose (4 reads better
    than −4); `dim_retail_product.units_returned` kept the raw negative until
    2026-08-18, so the two models disagreed about which way a return points.
    Nothing failed — the column had a `data_type` and no description — but the
    one place a reader would put them together, `units_returned / units_sold`,
    came out negative, and a bar of returns per product drew below the axis. It
    is 1..80,995 now, with an `accepted_range` holding it. A sign convention that
    is only written down in one of the two models that use it isn't one.
  - **`item_type` is not decoration.** A bare revenue sum carries £463,931 of
    postage and −£338,803 of bank fees as if they were sales.
- **Returns have no foreign key**, so `fct_retail_returns` infers the link with
  an `asof left join` to the same customer's most recent earlier purchase of the
  same product. 87.6% match cleanly, 2.0% match a *smaller* purchase, 8.4% have
  no prior purchase in the window, 1.9% have no customer id. **The 2.0% is the
  interesting number**, not the 87.6% — it is the rule being wrong rather than
  the data being absent. Reported per row instead of tuned into one headline; the
  median return comes back in 10 days, which is the evidence the rule isn't
  latching onto arbitrary sales.
- **`dim_retail_customer` covers a subset of the business and says so.** 22.8%
  of lines have no customer id — £2.67M, 13.8% of revenue. The two shares differ
  because an order nobody signed in for is a smaller order, and quoting the line
  share as the revenue share overstates the hole by nine points. Also: 5,881 of
  the 5,942 ids reach the dimension (61 never purchased), and `cohort_month` is
  the first *purchase*, not the first appearance.
- **The cohort triangle is ragged and left-censored, and both are columns.** A
  cohort born in November 2011 has no month-12 row — that is an absence, not a
  zero, so rows are generated only up to the last observable month.
  December 2009 is the extract's first month, so its "new" customers include
  everyone already buying; `is_left_censored_cohort` excludes them everywhere.
  Retention is against the cohort's own size, never against the previous month.
  - **Read a triangle by direction: down a column is ageing, along a diagonal is
    the calendar.** The heatmap's dark band is a diagonal — autumn. Pooled over
    every cohort age, a customer is active in 23.6% of September–November months
    against 15.3% for the rest of the year (Jan 11.2%, Nov 27.0%). The month-12
    "recovery" in the average curve is the same fact edge-on, since a cohort's
    twelfth month lands in the calendar month it was born in. A retention *curve*
    averages the diagonal into the column and reports the blend as ageing.
  - `retail_max_cohort_age_months` (36) is a var, not a literal, because it is
    the one number that can silently **truncate** the answer.
    `fct_retail_cohorts_are_not_truncated` asserts the first cohort reaches the
    last month.
- **`analytics.retail_rfm` is where the Polars layer stops being a division.**
  The operation is "cut a column into quintiles", and SQL's primitive for it —
  `ntile(5)` — is *wrong*: it fills buckets of equal size, so it cuts through a
  run of equal values wherever the boundary lands. 1,626 customers have placed
  exactly one order, and across the four tied values that straddle a boundary
  **3,227 of 5,881 customers** could be scored differently from someone whose
  behaviour is identical. `qcut` cuts on break points, so equal values always
  score equally and the buckets come out uneven — which is a fact about the
  customer base, not an artefact. `rfm_scores_do_not_split_ties` is a blocking
  asset check, because a regression to `ntile` still yields five tidy buckets and
  a plausible segment mix.
  - **Casting a Polars Categorical straight to an integer gives the physical
    dictionary index**, i.e. order of first appearance, not label order. Via
    `String` is the only reading that means what it says.
  - **Monetary is null for 28 customers, and the check now says so rather than
    not looking.** `monetary_gbp` is `net_revenue_gbp`, which `dim_retail_customer`
    already publishes as null for the customers whose orders held no revenue
    line — so `qcut` returns null and `concat_str`/`+` propagate it into
    `rfm_cell` and `rfm_total`. Kept null rather than coalesced to 0: a 0 scores
    them into the bottom quintile, which reads as "measured, worth nothing"
    instead of "nothing to measure". `segment` is unaffected, because the grid is
    R and F only. Two things this cost: `rfm_scores_do_not_split_ties` tested
    only `segment is null` and so could not see any of it, and Polars sorts nulls
    **first**, so a descending sort opened the table with the 28 least
    informative rows in the file. `nulls_last=True`, and the check now asserts
    the nulls fall exactly where monetary is null and nowhere else.
  - **`as_of_date` is a required parameter with no default.** Recency against
    `date.today()` makes every customer in a 2011 extract equally and
    enormously lapsed, and the segmentation quietly becomes a frequency ranking.
    It is read from the data and shipped as a column.
  - **The segment map is a 25-cell grid, not a rule list.** The widely-copied
    version ("Champions: R>=4 and F>=4", "Loyal: R>=3 and F>=3", …) has
    *overlapping* conditions, so the label depends on branch order — invisible in
    review. Monetary is deliberately not in the grid: R and F say what the
    relationship is doing, M says what it is worth.
  - Champions are 14.9% of the identified base and 62.7% of its revenue.
- **`dim_retail_customer.first_order_gbp` is the page's only forward-looking
  number, and the statistic that describes it is not the obvious one.** Pearson
  *r* against lifetime value is 0.641 and is almost entirely one customer: drop
  the single largest first order (£33,168 → £235,833) and it falls to **0.397**;
  under £5,000 it is 0.344. The **rank** correlation does not move — 0.592,
  0.592, 0.590 across the same three cuts — so that is what the page quotes,
  with the quintile medians (£191 → £410 → £714 → £905 → £1,885, repeat rate
  58% → 78%) as what it means in money. A Pearson *r* on a heavy-tailed money
  column is a statistic about the tail.
  - **An invoice, not a day.** `n_orders` counts invoices, so "order" has to
    mean the same thing in both columns — and 393 of the 5,881 customers bought
    twice on the day they arrived, so the choice moves the number. Ranking is on
    `min(invoice_ts)` (83 invoices carry more than one timestamp) then on
    `invoice`, because 11 customers opened two at the same minute and a
    non-deterministic tie-break is a column that changes between builds.
  - **Null for the 47 whose first invoice held no product line** — a `Manual`
    adjustment or the test SKU. Same `filter (where is_revenue_line)` shape as
    `net_revenue_gbp`, which is already null for 28 customers, so this is the
    model's existing convention rather than a new one.
  - **`first_order_gbp <= net_revenue_gbp` is true by construction and fails as
    a test, on 272 rows.** Every one is a one-order customer where the two
    columns are the same money summed in a different order; the excess tops out
    at 1.8e-12. Comparing two independently-summed doubles for containment is a
    float-equality test wearing an inequality. The shipped test is
    `accepted_range {min_value: 0}`, which is the same guarantee without the
    arithmetic.
  - **The chart cost four Evidence gotchas**, all written up in
    `reports/README.md`: a scatter over ECharts' `progressiveThreshold: 3000`
    never finishes rendering, `yLog=true` in markdown is the *string* `"true"`
    and leaves the axis linear, there is no `xLog` prop at all, and `> 0` is not
    a safe filter for a log axis when two customers carry 1e-14 of float residue.
  - `ntile(5)` is correct for the quintile table and wrong for the RFM scoring
    two sections below it, which is worth reading as a pair. A near-continuous
    currency column has almost no ties to split; `frequency` has 1,626 customers
    on one value.
- **The FX carry-forward stops being theoretical here.** 139,658 lines (13.1%)
  convert on a rate the ECB published earlier, and **every one is a Sunday**:
  this business trades Sunday and not Saturday (139,256 lines against 402), and
  it closes on exactly the days TARGET does, so no weekday closure ever
  coincides with an order. A model assuming "weekend" means Saturday and Sunday
  equally reads it backwards.
- **`fct_retail_order_line` carries a plain `year` beside `iso_year`**, because
  the lake partitions on it. The two agree on every row *only* because the
  business shuts 23 December to 4 January, so nothing lands on the three days a
  year where ISO week 1 crosses the new year. Partitioning on `iso_year` would be
  correct today and wrong the first New Year they trade.
- **It is the only table whose lake partitioning is sensible**: 1.07M rows over
  three years is three files of 13 MB / 12 MB / 836 kB, against the CO2
  archive's 275 files averaging 47 kB. The grain is a transaction and the
  partition is a year, so the ratio is 350,000:1 rather than 150:1.
- **The fixture is selected by shape, not sampled.** A 4% random draw keeps the
  volume and loses all six `A` adjustments and the single positive `C` line.
  `RETAIL_FIXTURE_SELECTION` in `scripts/record_fixtures.py` picks each shape
  explicitly and `tests/test_fixtures.py` asserts they survive. At 1.88 MB it is
  the largest file in the repo.

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

## Contracts, ownership and versions (`_groups.yml`, `_exposures.yml`)

Who owns each model, who may depend on it, what shape it promises, and who is
reading it. All four are declarative and all four are enforced by something —
the point of the layer is that none of it is a comment.

- **Groups are by domain, not by layer, or the boundary means nothing.** Four
  groups (`reference`, `country_stats`, `compliance`, `retail`) in
  `dbt/models/_groups.yml`; a staging/marts split would put every staging model
  in one group and nothing would ever cross it. One person owns all four and the
  file says so rather than inventing a team.
- **Staging is `private`, marts are `public`, and the two exceptions are the
  whole content.** The defaults are set per folder in `dbt_project.yml`;
  `stg_country` and `stg_energy` override to `protected` because they are the
  only two places one domain reads another's cleaning layer — the country
  dimension has no mart above it, and `dim_grid_emission_factors` deliberately
  re-models `stg_energy`'s intensity column for a different reader and needs
  `source_loaded_at` with it. Both reasons are written next to the override.
  Enforcement is real and was verified by breaking it: flipping `stg_country` to
  `private` fails `dbt parse` naming `fct_cbam_exposure`, not `dbt build` an hour
  later.
- **Marts are `public` because the release makes them so.** Every mart ships as a
  standalone Parquet file to people who cannot be paged; `access` is a statement
  about that, not about the repo.
- **Contracts are enforced on all 17 marts — 327 columns, each with a
  `data_type`.** The ymls documented 179 of those columns before, so the list was
  *generated* from the built warehouse's `information_schema` and inserted
  line-wise into `_marts.yml`, reordering the existing entries into SQL order and
  keeping every description untouched. A PyYAML round-trip would have reflowed
  1,246 lines of prose to add scalars; don't do that to this file.
  - **The grain contract and the schema contract catch different things.**
    `unique_combination_of_columns` has been holding the grain since the start;
    what it never saw was a column changing type under a consumer. Verified by
    declaring `year` as `VARCHAR`: the build fails with a per-column mismatch
    table before writing anything.
  - **A contracted incremental model must set `on_schema_change`.** dbt refuses
    `ignore` and it is right to — the contract promises a shape and `ignore`
    would let a column stop being written into the existing table.
    `fct_fx_rates_published` uses `fail` rather than `append_new_columns`,
    because a new column there means the *model* changed and 265k rows need a
    `--full-refresh` decision made by a person.
  - **CI builds the same types.** The declared types come from the full
    warehouse; CI builds the 17-country fixture slice, so a column whose type is
    inferred from data could have differed. `just test-pipeline` was run to check
    it rather than assumed — all 17 contracts hold on the slice.
- **Exposures are per *page*, not per site, and they are checked.**
  `dbt/models/_exposures.yml` declares eight Evidence pages and the monthly data
  release, so `dbt ls --select +exposure:evidence_retail` answers "what breaks if
  I change this" for one page. `scripts/build_report.py` gained `page_tables()`
  (page → source query → warehouse table) and `tests/test_exposures.py` fails if
  a declaration and the SQL disagree.
  - **They do not replace `TABLE_TO_DBT_MODEL` / `TABLE_TO_ASSET_KEY`, and the
    test says why.** An exposure can only name nodes dbt builds, so nothing in
    `analytics` (written by Polars, downstream of dbt and invisible to it) can
    appear in one. `reports/pages/pipeline.md` therefore has **no exposure at
    all** — every table it reads is a Polars output and `depends_on` cannot be
    empty. The test asserts that the remainder is *exactly* `TABLE_TO_ASSET_KEY`,
    so the gap is measured instead of forgotten.
  - **Two pages have no exposure and the test names both, because the reasons are
    opposite.** `pipeline.md` reads tables dbt cannot describe; `index.md` reads
    nothing at all — it is a routing page, prose and links, with no SQL on it, so
    there is no dependency to declare and no figure that can go stale by hand.
    Through `page_models()` the two are indistinguishable (both an empty set), so
    `test_the_pages_with_no_exposure_are_the_two_that_cannot_have_one` asserts the
    *table* counts as well: `index` must read zero source queries and `pipeline`
    must still read some. Put a chart on the front page and it fails.
  - **The release exposure names `stg_country`**, the one staging model in the
    list, because the release notes point a reader at `staging.stg_country` by
    name. The other seven staging views ship as Parquet too and nothing promises
    them.
- **`fct_emissions_energy` is versioned, and it is the right model rather than
  the biggest.** Nothing in the project refs it and the release ships it, so a
  rename is free in-repo and breaking outside it. v2 renames `co2_per_gdp` to
  `co2_kg_per_gdp_ppp_2011` — the old name gave neither unit nor basis while a
  differently-based intensity column sat one schema away.
  - **v2 is aliased back to the bare relation name.** The Evidence source query,
    `ARCHIVED_TABLES`, `TABLE_TO_DBT_MODEL` and the release notes all say
    `marts.fct_emissions_energy`; a migration that renames the table out from
    under them is not a migration.
  - **v1 is a view over v2, not a second copy of the model.** `select * exclude
    (…), … as co2_per_gdp` — one column put back, no duplicated logic and no
    duplicated 43k rows. It puts the renamed column *last*, and the v1 contract
    is declared the same way (`include: all`, `exclude:` the new name, then the
    old one appended) so the two agree on the order dbt enforces.
  - **`deprecation_date: 2026-11-01`** is carried in the release notes as well as
    the yml, because the consumers who need it never see a dbt log.
  - **Versioning a model changes its Dagster asset key, silently.**
    `default_asset_key_fn` keys an ordinary model on `[configured_schema, name]`
    (`marts/fct_emissions_energy`) but a versioned one on `[alias]` alone — so
    adding `versions:` renamed the asset to `fct_emissions_energy` and gave v1
    the sibling key `fct_emissions_energy_v1`. Both still run. What breaks is
    everything that spells the key out: `just materialize-select 'marts/*'` stops
    matching, and Dagster's materialisation history is keyed on the asset key, so
    the model looks like it has never been built. `FolderGroupDbtTranslator.get_asset_key`
    puts the schema back for versioned nodes, which is what keeps the version
    invisible to the rest of the graph.

## Pipeline observability (`transform/pipeline_status.py`)

`just pipeline-status` writes three flat tables into `analytics` —
`pipeline_sources` (dlt load time, rows and year span per landing table),
`pipeline_tables` (rows and year span per modelled table) and `pipeline_tests`
(every dbt test, what it guards, and how many rows are currently failing it).
`reports/pages/pipeline.md` renders them; the asset is
`analytics/pipeline_status`, downstream of **both** Polars assets
(`co2_intensity` and `retail_rfm`). It has to name both: it inventories
`analytics`, and depending only on the one that sits furthest downstream would
leave the other free to land after the inventory meant to count it.

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
- **A test's verdict is its `fail_calc`, not `count(*)` over the audit table.**
  `count(*)` is only dbt's *default*. `dbt_utils.equal_rowcount` overrides it with
  `sum(coalesce(diff_count, 0))` and returns a one-row summary whether it passed
  or failed, so counting rows scored both `equal_rowcount` guards (on
  `fct_fx_rates_published` and `fct_retail_order_line`) as one failing row each
  against a build that finished ERROR=0 — the health page contradicting the
  build. `build_tests` reads `fail_calc` from the manifest and applies it, which
  is what dbt does; 366 of the 368 tests use the default. `severity` comes across
  the same way, so a `warn` test with failures is `status='warn'`, not `'fail'`.
- **An audit table the manifest doesn't name is stale and is dropped.** dbt writes
  that schema every build but never *removes* a table whose test is gone, and the
  alias hash is over the test's arguments — so renaming a model orphans every
  audit table on it. Versioning `fct_emissions_energy` to `_v2` left 17
  `dbt_utils_accepted_range_fct_e_<hash>` tables behind; being empty they scored
  as passing and inflated the count to 371 against dbt's 354. The filter is keyed
  on the manifest being *present*, not on the match: with no manifest nothing
  matches and dropping everything would empty the table rather than degrade to
  bare names.
- **It excludes its own output from the inventory.** Otherwise the table count
  jumps by three on every build after the first, for no change in the warehouse
  (30 tables today, not 33).
- **It must run after `dbt build`** — it reads `dbt_test__audit` and the
  manifest, neither of which exists before one.

## The lake (`lake/archive.py`)

`just lake` writes the year-keyed tables back out of DuckDB as hive-partitioned
Parquet under `data/lake/<table>/year=<year>/data_0.parquet` (zstd, gitignored,
793 files / ~60 MB today). It's part of `just run`, an asset
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
  of the 793 files. That is the point of the layer — the DuckDB file differs
  everywhere on every run, so it can't tell you what moved.
- **Most partitions are far too small for a real lake.** The CO2 archive is 275
  of them averaging ~47 kB, against a ~100 MB rule of thumb, and on object
  storage that is 275 round trips. The two exceptions are both tables whose
  grain is finer than their partition column, which is the whole rule:
  `marts.fct_fx_rates_daily` is 381k rows over 28 year-partitions, and
  `marts.fct_retail_order_line` is the best in the archive at 1.07M rows over
  **three** — 13 MB, 12 MB and 836 kB, i.e. 26 MB of a 60 MB archive in three
  objects. A transaction grain against a year partition is a 350,000:1 ratio
  where a country-year is 150:1. Year is the partition column anyway because
  it's the one every query filters on, and pruning still measures:
  `where year = 2020` runs in ~23 ms against ~50 ms for the whole archive.
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
  dlt's landing tables or the snapshot downloads the database.
- **Each release carries the previous one's `history` forward**
  (`scripts/restore_history.py`), which is what makes the published snapshot
  accumulate a real revision log instead of holding one version per row forever.
  The restore runs *before* the graph, writing a history-only DuckDB file that
  dlt then lands `raw` into — safe because dlt keys "is this destination fresh?"
  on its own bookkeeping inside `raw`, not on the file existing (verified: a
  fixture load into a restored file still fetched the full WDI series, not a
  five-year window).
  - **Only "no previous release" may skip.** A failed download or restore is
    fatal in `release-data.yml`: continuing would publish an empty history that
    the *next* release then inherits, which is the exact failure the step
    prevents. The verify step asserts the shipped snapshot is no smaller than
    what was carried in. `pages.yml` runs the same step `continue-on-error`,
    because there the snapshot is a read-only display and a missing release
    should cost one section of one page, not the deploy.
  - **It refuses to overwrite a destination that already holds history**, so
    running it against the real warehouse can't destroy months of local versions
    — `--force` if that is genuinely what you want. It also rejects a source
    table lacking dbt's SCD2 columns, which would otherwise fail later and much
    less legibly inside `dbt build`.
  - Verified end to end against fixtures rather than by waiting for OWID: export
    release 1, restore into a fresh warehouse, restate three country-years in
    `raw.owid_co2`, rebuild — 595 snapshot rows became 598 and
    `fct_co2_estimate_versions` showed the three at version 2.

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
  window is never seen — `just ingest-wdi-full` (`INGEST_WDI_FULL=1`) re-fetches
  everything, and `just backfill-wdi 1997` re-fetches exactly that year through
  the partitioned asset. The window and the partitions sit *beside* each other on
  purpose: the daily path stays cheap and unattended, and reaching further back
  is an explicit act with a key you can point at. dlt resets its own state when the destination is empty, so
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
- **A resource that yields Arrow gets no `_dlt_load_id` unless you ask.**
  `retail_invoice_lines` yields Arrow batches straight out of DuckDB, and dlt's
  Parquet normalizer leaves the load-id column off by default — so the biggest
  table in the warehouse landed with no load provenance, `dbt source freshness`
  had nothing to read, and `pipeline_sources` silently reported six sources for
  seven. `build_pipeline()` sets
  `NORMALIZE__PARQUET_NORMALIZER__ADD_DLT_LOAD_ID=true`. Adding the column to an
  existing table needs a `drop table` plus `refresh="drop_resources"`; dlt will
  not widen into it.
- **`.arrow()` is a streaming reader with a 1,000,000-row default batch.** Using
  it to hand a relation to dlt stored exactly 1,000,000 of 1,067,371 rows, with
  no error — the round number was the only clue. `to_arrow_reader(BATCH)` and
  `yield from` is the fix, and `test_retail_yields_every_row_the_workbook_holds`
  counts.
- **Declare `timezone: False` on a timestamp column, or dlt makes it
  `TIMESTAMP WITH TIME ZONE`.** A 07:45 till time then reads `08:45:00+01:00` on
  a CET machine and differs between a laptop and CI.
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
- **Two carbon-intensity columns, different bases.**
  `fct_emissions_energy.co2_kg_per_gdp_ppp_2011` is OWID's kg CO2 per 2011
  international-$ (PPP) and stops in 2022 / 164 countries.
  `analytics.co2_intensity.co2_per_gdp_const_usd` is derived in
  `transform/co2_intensity.py` and tracks the mart — ~197 countries through 2024,
  but only back to 1960, where WDI starts. Levels aren't comparable between the
  two; the rank uses only the derived one. The mart's column was called
  `co2_per_gdp` until the v2 rename, which is the whole reason that model is
  versioned — see *Contracts, ownership and versions* above.
- **Divide by `gdp_constant_usd`, never `gdp_usd`, for anything measured over
  time.** `gdp_usd` (`NY.GDP.MKTP.CD`) is *current* US$, so it moves with
  inflation and the exchange rate: on that basis Japan cut emissions 21% from
  2010–2024 and still scored 10% *worse* on carbon intensity, purely because the
  yen fell 28% against the dollar. `gdp_constant_usd` (`NY.GDP.MKTP.KD`, constant
  2015 US$) is the real-terms series. Current US$ is fine for single-year
  cross-sections, wrong for trends. **The same failure is now measurable rather
  than narrated** — see the Currency section: the EU household electricity price
  rose 35% or 13.5% between 2021-S1 and 2022-S2 depending only on whether you
  counted in euros or dollars.
- **World Bank WDI** is fetched long (one row per indicator/country/year) and
  pivoted to wide columns in `stg_wdi.sql`. Add indicators in two places:
  `WB_WDI_INDICATORS` in `ingest/pipeline.py` and a `max(case …)` in `stg_wdi.sql`.
- **Eurostat is JSON-stat** — a flat `value` dict keyed by a row-major index over
  all dimensions. `eu_elec_prices` filters every dimension but `geo`/`time`
  server-side, then walks that grid (see `pipeline.py`). Its `geo` codes are ISO2
  *except* `EL`=Greece (GR) and `UK`=UK (GB);
  `stg_eu_electricity_prices_semiannual.sql` remaps those and joins `stg_country`
  for ISO3. EU/EEA only, so the mart column is null for the rest of the world.
  (The `length(geo) = 2` filter there drops `EU27_2020` and friends but *not*
  `EA` — two letters. That falls out at the inner join, which no ISO2 matches.)
- **Eurostat prices are semi-annual, and both grains are modelled.**
  `stg_eu_electricity_prices_semiannual` is the cleaning model at
  `(country_iso3, year, half)`; `stg_eu_electricity_prices` averages it to annual
  so it can join the country-year spine. Averaging is what the annual grain costs,
  and the cost is large enough to model around: the mean absolute half-over-half
  change was 19% across countries in 2022 and 13% in 2023 against 3–4% through the
  2010s, and the Netherlands went €0.034/kWh in 2022-S1 to €0.142 in S2 (+320%) as
  that year's energy-tax cuts landed in the first half. The annual €0.088 is a
  price nobody paid. Chart prices *over time* off
  `marts.fct_eu_electricity_prices_semiannual`; use the annual column only to
  join prices to emissions or GDP.
- **An "annual" price can be one half-year.** Eurostat publishes S1 around May and
  S2 the following spring, so `n_half_years` (staging) / `price_is_partial_year`
  (mart) exist to say when the average is over one half. It is *not* only a
  latest-year edge case — 29 country-years carry the flag, including 23 countries
  at the 2007 series start and one-offs like the UK in 2020 and Iceland in 2025.
  `sources/warehouse/latest_years.sql` counts only complete years for
  `price_year`, and the dashboard reports the partial count for the selected year
  rather than dropping those countries (in 2007 that would drop 23 of 27).

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
- **Everything runs in one process** (`in_process_executor`, and the four
  `replace` dlt resources in a single op). DuckDB takes one writer at a time, so
  parallel steps would just fight over the file lock.
- **`raw/wb_wdi` is partitioned by year and nothing else is** — which is why it
  sits in its own `@dlt_assets` block (`ingest_wdi`). Dagster gives every asset
  in a multi-asset the same `partitions_def`, and four of the other five sources
  are whole-file `replace` downloads with no per-year fetch to express, so
  partitioning them would be a fiction. WDI earns it: the API takes `&date=lo:hi`,
  the disposition is `merge`, and `year` is in the primary key, so a partition is
  a real re-runnable unit of work.
  - **Merging is not what earns a partition, and `ecb_fx_rates` is the near-miss
    that proves it.** It is incremental *and* its API takes a date range, so by
    the letter of the paragraph above it qualifies — but its entire 27-year
    series is one three-second request, and partitioning it would trade that for
    thousands of Dagster partitions and buy nothing. So the blocks split on
    `PARTITIONED_RESOURCES`, not on the disposition, and
    `UNPARTITIONED_RESOURCES` in `orchestration/assets.py` is derived as
    everything else. **Before this there were two tuples and the WDI block was
    built from `INCREMENTAL_RESOURCES` directly** — adding a second merge
    resource to that constant would silently have given it yearly partitions.
    `load_groups` still owns the refresh/merge split; the blocks only decide who
    gets a `partitions_def`. Two tests in `tests/test_ingest.py` hold both
    splits to the source.
  - **The asset has two paths and the unpartitioned one has to keep working.**
    `full_refresh` contains this asset and `ci.yml` / `nightly.yml` /
    `release-data.yml` execute that job with no partition key. A partitioned
    asset in an unpartitioned run doesn't fail at plan time — it fails *inside
    the body*, at the first touch of `context.partition_key`. So the fallback is
    an explicit guard in the asset, not something the job gives you: no partition
    means the incremental lookback, exactly as before.
  - **Guard on `has_partition_key` *and* `has_partition_key_range`.**
    `has_partition_key_range` is False for a run targeting a single partition, so
    testing it alone makes `--partition 1995` fall through to the incremental
    branch — and it *succeeds*, having loaded the wrong window. (Verified by
    doing it.) `context.partition_key_range` itself covers both cases; it returns
    `start == end` for one key.
  - **A backfill deliberately doesn't move the WDI watermark.** The watermark
    means "everything up to here is loaded", which a run over one window can't
    claim: partitions 2020–2025 into an empty warehouse would otherwise leave a
    2025 watermark and the next incremental run would look back five years over
    sixty years that were never fetched.
  - **`end_offset=1`, or the current year isn't a partition.** A yearly window
    only closes on 1 January, so the newest partition would be last year — the
    one you actually want to re-run wouldn't exist.
  - **`end` is exclusive, and the retail partitions were short a month because of
    it.** `TimeWindowPartitionsDefinition(start=RETAIL_FIRST_MONTH,
    end=RETAIL_LAST_MONTH)` reads like a closed interval and is not one: it
    resolved to 24 keys ending at `2011-11`, so December 2011's 25,526 lines had
    no partition to land in and no key that could ask for them. Nothing was ever
    red — every workflow and justfile recipe uses the *unpartitioned* path, which
    loads the whole workbook — so the only symptom was a per-partition backfill
    quietly stopping a month early. `_month_after(RETAIL_LAST_MONTH)` is the
    fix, keeping the constant meaning the data's last month, and
    `tests/test_definitions.py` now pins both ends and the key count.
    `end_offset=1` above solves the same off-by-one for the open-ended source;
    this is the closed-archive half of it.
  - `BackfillPolicy.single_run()` (which a `TimeWindowPartitionsDefinition` also
    defaults to) is what makes a range one request per indicator instead of one
    per year: 1990–2025 is 11 requests, not 396. It also means the CLI's
    `--partition-range` refuses any selection that reaches the *unpartitioned*
    downstream models, so `just backfill-wdi` targets `raw/wb_wdi` alone and you
    rebuild after it.
  - Partition status starts empty even though `raw.wb_wdi` holds the full series:
    the rows came from unpartitioned runs. That's cosmetic — the merge key, not
    Dagster's partition record, is what makes a re-run idempotent.
- **Every asset and check is listed by hand in `definitions.py`, and nothing
  tells you when one isn't.** `dg.Definitions` takes explicit lists, so an
  omission is not an error — the asset is simply not in the graph,
  `AssetSelection.all()` never sees it, and `dagster definitions validate`
  passes. That is how `raw/retail_invoice_lines`, `analytics/retail_rfm` and two
  asset checks sat unregistered from the retail and currency commits until
  `full_refresh` failed in CI with `Catalog Error: Table with name
  retail_invoice_lines does not exist!` — one layer downstream, in dbt, naming
  the symptom and not the cause. The two checks failed more quietly still: an
  unregistered check just never runs. `tests/test_definitions.py` now compares
  what `assets.py` defines against what the graph resolves, and CI runs it in the
  `dbt parse` step (it needs the manifest, so it skips itself in `just test`).
  - **Compare *executable* asset keys, not `get_all_asset_keys()`.** An
    unregistered asset that something depends on still appears in the graph as an
    external node, so the wider set reports `analytics/retail_rfm` present purely
    because `pipeline_status` names it in `deps` — a test that passes while the
    pipeline is broken. Same trap one level up: `full_refresh`'s job graph
    contains `raw/retail_invoice_lines` as an unexecutable node.
  - **`AssetChecksDefinition` is a subclass of `AssetsDefinition`.** An
    `isinstance` chain that tests the parent first swallows every check into the
    asset branch, where `.keys` is empty — the check half of the test then
    measures nothing and is green forever.
- **An asset job may not span two partitions definitions, which is why there are
  three jobs.** `raw/wb_wdi` is yearly and `raw/retail_invoice_lines` is monthly;
  `define_asset_job` resolves its selection to a single `partitions_def` or
  raises. There is no opt-out — `allow_different_partitions_defs` is hardcoded
  `False` for named asset jobs and `True` only for Dagster's own implicit global
  job. So `load_retail` carries the retail ingest alone, `full_refresh` is
  `AssetSelection.all() - site - retail_ingest`, and **`load_retail` has to run
  first** because dbt reads the table it lands. The justfile recipes and all four
  workflows pair them; running `full_refresh` by itself against a fresh warehouse
  reproduces the catalog error above.
  - `dagster asset materialize --select '*'` is not a way round it: the CLI
    refuses a partitioned asset without `--partition` ("Asset has partitions, but
    no '--partition' option was provided"), so the unpartitioned whole-graph run
    only exists as a job.
  - **A job shares a namespace with the ops**, so the job is `load_retail` and
    not `ingest_retail` — `@dlt_assets(name="ingest_retail")` already holds that
    name, and the collision reports as `Conflicting definitions found in
    repository with name 'ingest_retail'` naming `__ASSET_JOB`.
- **The Evidence site is an asset, and it's the asset excluded from
  `full_refresh` for a reason that isn't partitioning.** `reports/evidence_site`
  shells out to npm via `scripts/build_report.py`; `ci.yml`, `nightly.yml` and
  `release-data.yml` all run `full_refresh` on a bare uv checkout with no Node, so
  a site in that job would break three workflows to serve one. `pages.yml` runs
  `publish_site` — one job instead of the three npm steps that used to sit after
  it. Both selections in `definitions.py` now name what they leave out; a second
  npm-shaped *or* differently-partitioned asset would have to be excluded by hand
  too.
- **Importing `orchestration.assets` leaves a dlt pipeline active process-wide.**
  The `@dlt_assets` decorators call `build_pipeline()` at import time and dlt
  records the result as the ambient pipeline, so a later test calling a resource
  generator directly reads the real `~/.dlt` state instead of none —
  `test_wb_wdi_follows_pagination` starts asking for `&date=2021:2026` and fails
  on pagination it never got wrong. It only bites when the whole suite runs, and
  only on a machine that has loaded WDI at least once. `tests/test_definitions.py`
  deactivates the pipeline on teardown; anything else under `tests/` that imports
  the orchestration layer has to do the same.
- **The site's deps are one per table it reads, not the single ordering edge.**
  `scripts.build_report.TABLE_TO_DBT_MODEL` / `TABLE_TO_ASSET_KEY` map the 20
  tables the source queries read to the assets that write them, and
  `tests/test_report.py` parses `reports/sources/**/*.sql` and fails if the two
  disagree. Without it, adding a source query on a new mart would leave the site
  building from a stale copy of it while the graph still showed complete lineage —
  no error, just an old number. The maps live in `scripts/` rather than beside the
  asset because `just test` runs before `dbt parse` in CI and so can't import
  anything that needs the dbt manifest.
- **`site_pages_all_rendered` is blocking, and it checks file *size*.**
  `evidence build` exits 0 for a site missing a page, and nothing downstream reads
  `reports/build/` — so a route that emitted only the SvelteKit shell would
  materialise green and deploy. The ten pages render at 19–92 kB; the floor is
  8 kB. The two smallest are the ones carrying the least SQL — the routing front
  page (19 kB) and Restatements (20 kB) — so it is prose-only pages, not chart
  pages, that would ever bring the floor into play.
- **`explore`, `settings` and `api` are reserved route names.** Evidence's own
  template ships `pages/explore/` (the SQL console and schema browser) and
  `pages/settings/`, so a `reports/pages/explore.md` is silently *not* copied into
  `.evidence/template/src/pages/` and the build dies on
  `Internal Error /api/[...route]/evidencemeta.json … /api/explore/… status 500`
  — a message that names neither the page nor the collision. `just report-clean`
  does not help, because nothing is stale. The country explorer is
  `pages/countries.md` for exactly this reason.
- The `daily_refresh` schedule ships `STOPPED` on purpose — opening the UI
  shouldn't start hammering public APIs on a timer. It targets `full_refresh`, so
  it doesn't try to build the site either.
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
- **Three fixtures aren't trimmed at all**, each for a different reason:
  `wb_country` because it *is* the dimension the overrides seed is diffed
  against, `eu_elec_prices` because a JSON-stat grid can't be subset without
  rebuilding its index, and `ecb_fx_rates` because the interesting structure is
  *when each currency starts and stops* — cutting the date range would take the
  euro changeovers, the rouble and Iceland's nine-year gap out of CI, which are
  the four shapes the FX models exist to handle. It is gzipped (3.6 MB → 843 kB),
  and it is why `_get_json` has a `.gz` branch.
- **`fixtures.path_for()` raises on an unmapped URL** rather than falling back to
  the network — otherwise "offline CI" quietly becomes "CI that's online
  sometimes". `tests/test_fixtures.py` asserts every URL the pipeline can build
  resolves to a file that exists.
- **dlt wraps anything a resource generator raises** in `ResourceExtractionError`,
  so tests asserting on ingest errors match that, not the underlying exception.
- **The retail workbook cache is keyed on the archive's content, and had to be.**
  `retail_workbook()` unzips a 45 MB workbook into `data/cache/{fixtures,live}/`
  and used to key that on the directory alone — if the `.xlsx` was there, it was
  returned. Nothing could then notice that the zip *underneath* it had changed,
  so `just record-fixtures` rewriting `retail_online_retail_ii.zip` left the
  previous slice in place and every fixture test went on passing against data the
  repo no longer contained — a re-recording that looks like a no-op is the worst
  possible shape for this. A sha256 prefix in the path makes a re-record a cache
  miss. This is the same failure as the `fixtures`/`live` split one level in, and
  the same family as the `_fixtures` dlt pipeline-name suffix: a cache whose key
  doesn't include everything the value depends on. Stale digest directories are
  left rather than pruned — the cache is gitignored and safe to delete.
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

Exported Claude Code session logs go in `docs/sessions/`, which is **gitignored
in full** — nothing there is in the repo, so a fresh clone has no such directory.
Transcripts are a local working record, not project history: they're long, they
duplicate what the commits already say, and while the directory held both tracked
and untracked files a `git add -A` could commit scratch notes that were never
meant to ship. **Anything learned in a session that should outlive it belongs in
this file**, which is the part of that history meant to survive.
