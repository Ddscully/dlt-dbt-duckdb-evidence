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
| `just dbt-build` | `dbt deps` then `dbt build` (25 models, 2 snapshots, 5 seeds + 337 tests) |
| `just dbt-freshness` | `dbt source freshness` — is the warehouse stale? |
| `just transform` | Polars derived metrics → `analytics` schema |
| `just pipeline-status` | load times, layer inventory, dbt test state → `analytics.pipeline_*` |
| `just lake` | year-partitioned Parquet archive of the warehouse → `data/lake/` |
| `just run` | ingest → dbt-build → transform → pipeline-status → lake (shell ordering) |
| `just dagster` | Dagster UI on :3000 — asset graph, runs, freshness, checks |
| `just materialize` | same pipeline, ordered by the asset graph (`full_refresh`, no Evidence) |
| `just materialize-site` | `full_refresh` + the Evidence site (`publish_site`; needs Node) |
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
- **`astral-sh/setup-uv` is pinned to an exact patch, not a major.** It stopped
  publishing moving major/minor tags at v8 as a supply-chain measure, so `@v9`
  does not resolve at all. All four workflows carry a comment saying so, because
  the obvious tidy-up is to "simplify" it back to a major.
- **`pages.yml` is the only workflow that needs Node** (24; the Evidence build).
  The other three run on a bare uv checkout — see the Orchestration section for
  why the site is excluded from `full_refresh`.

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
  `fct_emissions_energy` (the wide join, built on the spine),
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

Annex I of Implementing Regulation (EU) 2025/2621 — the country x good default
values an importer uses from 2026 when they have no verified supplier data —
transcribed into two seeds and multiplied by a carbon price. 11,657 rows over 119
countries and 264 goods. The only model here with **no year in its grain**: it is
a regulatory schedule, not a time series.

- **A seed, not a dlt resource, and that is the interesting decision.**
  Regulatory reference data is versioned by *amendment*, not by scrape; there is
  no API, and the values change when a new implementing regulation says so.
  `scripts/build_cbam_seeds.py` regenerates both seeds from the Commission's
  published workbook, so the next amendment is a re-run and a reviewable diff
  rather than a re-transcription. `country_overrides.csv` is the precedent.
- **Two seeds because normalising the goods out is worth 1.6 MB.** 12,532 value
  rows share 287 (product group, CN code, description) triples and one
  description runs to 250 characters. `cbam_goods` is 38 kB; inlined it would be
  1.6 MB of CSV and the same again in the warehouse.
- **A CN code is not a key.** 2523 10 00 is both white clinker and grey clinker
  and their values differ by more than 2x, so the grain is (CN code, description)
  and `good_key` is a slug of the pair — readable in a diff, and stable across an
  amendment in a way a renumbered surrogate would not be.
- **The transcription is faithful, defects included, and the mart is where they
  are handled.** The annex is a legal instrument; cleaning it in the seed would
  put this project's judgement between the regulation and a euro figure. Four
  quirks, all verified as present in the OJ text and not introduced here:
  - Albania's white Portland cement is published with `-` for direct, indirect
    and total and its three values sitting in the *mark-up* columns instead. The
    extractor reads the mark-up columns only when a total is present, which lands
    the row on the annex's own "field shows `-` → use the fallback" rule.
  - Five cement rows (Angola, Argentina) **compound** the mark-up — x1.1, x1.21,
    x1.331 — where the other 10,926 add it. Flagged (`markup_schedule_is_irregular`),
    never corrected.
  - Chile's line pipe has a total and a blank 2026 cell. This is what proves the
    fallback is a **row-level rule, not a column-level one**: a per-column
    `coalesce` paired Chile's tonnage with the *fallback's* mark-up and produced a
    100% implied rate — a row that exists nowhere in the regulation.
  - 23 of the 287 goods carry no value in any country, including the fallback.
    They are 4-digit CN *headings* whose subheadings hold the numbers, and they
    are excluded from the mart — 875 rows that could only be priced at null.
- **Fertilisers carry a 1% mark-up in all three years**, not 10/20/30% and not
  1/2/3%. 2,416 rows, consistent across every country, so it reads as intended.
  The mart derives each group's rate with `mode()` rather than asserting the
  schedule — hardcoding 10/20/30 overstates every fertiliser line by nine points
  in 2026 and twenty-seven in 2028, and an amendment that moves a rate needs no
  edit. The same mistake in reverse broke `markup_schedule_is_irregular` first
  time round: extrapolating the 2028 rate from the 2026 one flagged all 2,457
  fertiliser rows as irregular when none are.
- **Round half up, not `round()`.** The OJ prints three decimals and the
  Commission's XLSX mark-up cells are live formulas, so they arrive as binary
  floats. Python's banker's rounding turns 7,7165 into 7.716 where the regulation
  says 7,717. Eleven rows across the seven spot-checked countries differed in the
  third decimal before `Decimal` + `ROUND_HALF_UP`. Small, but the column is
  multiplied by a carbon price and shown as money.
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
  country's steel default and its grid factor is 0.26.
- **Excel mangles the country names, so `country_display_name` exists.** Sheet
  names cap at 31 characters and forbid punctuation, which is why the annex's
  Congo arrives as `Democratic Republic of the Cong` and Myanmar as
  `Myanmar_Burma`. The seed keeps the annex's label because it is the legally
  meaningful one; the mart coalesces to `stg_country.country_name` for anything
  that goes on a chart.

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
- **It excludes its own output from the inventory.** Otherwise the table count
  jumps by three on every build after the first, for no change in the warehouse
  (29 tables today, not 32).
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
  - `BackfillPolicy.single_run()` (which a `TimeWindowPartitionsDefinition` also
    defaults to) is what makes a range one request per indicator instead of one
    per year: 1990–2025 is 11 requests, not 396. It also means the CLI's
    `--partition-range` refuses any selection that reaches the *unpartitioned*
    downstream models, so `just backfill-wdi` targets `raw/wb_wdi` alone and you
    rebuild after it.
  - Partition status starts empty even though `raw.wb_wdi` holds the full series:
    the rows came from unpartitioned runs. That's cosmetic — the merge key, not
    Dagster's partition record, is what makes a re-run idempotent.
- **The Evidence site is an asset, and it's the one asset excluded from
  `full_refresh`.** `reports/evidence_site` shells out to npm via
  `scripts/build_report.py`; `ci.yml`, `nightly.yml` and `release-data.yml` all run
  `full_refresh` on a bare uv checkout with no Node, so a site in that job would
  break three workflows to serve one. `publish_site` is `AssetSelection.all()` and
  `pages.yml` runs it — one job instead of the three npm steps that used to sit
  after it. `AssetSelection.all() - site` is the only selection in
  `definitions.py` that names what it leaves out; a second npm-shaped asset would
  have to be excluded by hand too.
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
  materialise green and deploy. The nine pages render at 19–83 kB; the floor is
  8 kB.
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
