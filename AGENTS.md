# AGENTS.md

Guidance for coding agents (and humans) working in this repo.

## Reading this file

This is the one instructions file for every agent. `CLAUDE.md` imports it and
adds only Claude Code's plugin declarations, and the project skills live in
`.agents/skills/` with `.claude/skills` a symlink to them — Claude Code reads
only its own directory, and Codex only this one.

- **It stays under 32 KiB, because that is all Codex reads by default**, and it
  cuts the rest with nothing but a trace-log line. `project_doc_max_bytes` is
  user configuration the repo cannot set, so `tests/test_agent_instructions.py`
  holds the size instead: a section that would push past it belongs in a skill.
- **Gemini CLI reads `GEMINI.md`** unless `context.fileName` in its settings
  names `AGENTS.md`.

## What this is

A public demo of a modern, lightweight data-engineering + BI stack. Everything
runs locally with `uv` against a single DuckDB file — no cloud warehouse.

```
dlt (EL) → DuckLake (raw) → dbt (staging/marts) → Polars (heavy T) → Evidence (BI)
   data/lakehouse/            └────────▶ data/warehouse.duckdb ─▶ the release
                    all orchestrated by Dagster
```

Starting a *different* project on this shape is
[`docs/REUSING_THIS_STACK.md`](docs/REUSING_THIS_STACK.md); the rest of this file
is about *this* warehouse.

**The README is the tour, and the explanation sits in `docs/`, one file per
topic** — [`WAREHOUSE.md`](docs/WAREHOUSE.md),
[`ORCHESTRATION.md`](docs/ORCHESTRATION.md),
[`DATA_QUALITY.md`](docs/DATA_QUALITY.md),
[`PUBLISHED_DATA.md`](docs/PUBLISHED_DATA.md),
[`DATA_PROTECTION.md`](docs/DATA_PROTECTION.md),
[`DASHBOARD.md`](docs/DASHBOARD.md) and
[`FOR_REVIEWERS.md`](docs/FOR_REVIEWERS.md). What it cost to learn sits here and
in the skills, so a change to how a layer works usually needs an edit in `docs/`
**and** in one of those.

- **[`PRACTICES.md`](docs/PRACTICES.md) restates figures from five other files**,
  and `tests/test_documented_counts.py` covers only its test, mart and additivity
  counts; any other claim added there is kept in step by hand.
- **[`RUNNING_AS_A_SERVICE.md`](docs/RUNNING_AS_A_SERVICE.md) mostly describes
  what the repo has not built.** Only its §2 exists, as `just serve`, and nothing
  checks the paths `docs/` cites.

Three lessons that apply well beyond where they were learned:

- **A design block nobody has executed is prose.** `just serve`'s first recipe
  carried two defects, both a service that looks healthy and is not, and neither
  was visible by reading; the doc's "Stopping it" section has the measurements.
- **The DuckDB lock is one writer XOR many readers**, across processes: a
  read-only connection fails while a build holds the file, and a build fails
  while anyone is reading it. `lake.lakehouse.read_only_connection()` is the one
  read that works mid-build (`querying-the-warehouse`).
- **`uv sync` strips the venv; `uv run` does not.** A bare `uv sync` installs
  `dev` alone and removes the `orchestration` group from under any running
  service. This file once said the opposite, citing a dry run of the other
  command: **a measurement of one command is not evidence about another.**

## The layers, and what each directory is for

```
ingest/     dlt — `sources/` is one module per publisher; `pipeline.py` is
            coordination (which resources exist, how they group, the pipeline)
lake/       the DuckLake landing zone
dbt/        staging → intermediate → marts
transform/  the Polars derived metrics
orchestration/  the Dagster asset graph over all of the above
publish/    the boundary outward: the Evidence site, the release, and the
            previous release's carried state
scripts/    genuinely one-off: seed transcription, fixture recording, a
            disclosure measurement
src/modern_data_stack/   the domain-neutral mechanisms every layer calls
```

- **`ingest/` is one module per publisher, split for cohesion rather than size,
  plus `pipeline.py`'s coordination tuples.** Shared helpers are reached as
  `http.get_json(...)`, never imported by name, because the tests patch them
  through the module; the rest is `adding-a-data-source`.
- **`publish/` is the boundary outward** — the personal-data policy, the storage
  ceiling, attribution — so `pages.yml` triggers on `publish/**`.

## The package (`src/modern_data_stack/`)

The domain-neutral mechanisms live here and take their configuration as
arguments; the project modules that call them (`lake/lakehouse.py`,
`publish/export_warehouse.py`, …) hold this project's constants and stay the
entry points. The module table and the rules behind the split are
[`docs/REUSING_THIS_STACK.md`](docs/REUSING_THIS_STACK.md). Two of them bite in
any change:

- **`modern_data_stack.paths` raises when it cannot find the project**, and a cwd
  fallback must not be added: it would resolve `./data/warehouse.duckdb`, which
  DuckDB *creates*, so a run outside the tree goes green against an empty
  database.
- **Config reaches a package module as a parameter, never as a constant.**
  Nothing under `src/` knows what a country is.

## Commands

Use the `justfile` recipes (they map to plain `uv run …` commands):

| Command | What it does |
|---------|--------------|
| `just setup` | `uv sync --group dev --group orchestration`, then `just extensions` — ducklake, httpfs and postgres, binaries no lockfile can name |
| `just compose-up` / `just compose-down` | the optional backing services — Postgres for the catalog, SeaweedFS for the Parquet (`compose-down volumes` destroys both) |
| `just ingest` | run the dlt pipeline → `raw` in the DuckLake catalog |
| `just ingest-wdi-full` | same, ignoring WDI's incremental watermark (full re-fetch) |
| `just dlt-state` | dlt's incremental state, which lives in `~/.dlt`, not the warehouse |
| `just dbt-deps` | install dbt packages (`dbt_utils`) into `dbt/dbt_packages/` |
| `just dbt-build` | `dbt deps` then `dbt build` (33 models, 2 snapshots, 8 seeds + 482 data tests + 36 unit tests) |
| `just dbt-unit-test` | the dbt unit tests alone — the inner loop for model logic |
| `just dbt-freshness` | `dbt source freshness` — is the warehouse stale? |
| `just dbt-docs` / `just dbt-docs-serve` | `dbt docs generate` to `dbt/target/`, and serve it on :8080 |
| `just transform` | Polars derived metrics → `analytics` schema |
| `just pipeline-status` | load times, layer inventory, dbt test state → `analytics.pipeline_*` |
| `just lakehouse` | report what the DuckLake landing zone holds — tables, rows, snapshots |
| `just run` | ingest → dbt-build → transform → pipeline-status (shell ordering) |
| `just dagster` | Dagster UI on :3000 — asset graph, runs, freshness, checks |
| `just materialize` | same pipeline, ordered by the asset graph (`load_retail` then `full_refresh`, no Evidence) |
| `just materialize-site` | the same two jobs + the Evidence site (`publish_site`; needs Node) |
| `just materialize-select 'raw/wb_wdi*'` | one asset + everything downstream (`*` all, `+` one layer) |
| `just materialize-preview '<sel>'` | what a selection resolves to, materializing nothing — zero matches still exits 0 |
| `just backfill-wdi 1990 1995` | re-load WDI for one year or a range — run config on `raw/wb_wdi`, not a partition |
| `just backfill-weather 2012 2026` | deepen the weather archive a year at a time, paced to Open-Meteo's budget: about an hour a decade, fifteen years at most per run |
| `just report` / `just report-clean` | build the Evidence site (`--clean` drops the schema cache) |
| `just serve` | the graph and the dashboard as one always-on service (`docs/RUNNING_AS_A_SERVICE.md`) |
| `just export-data` | package `data/export/`, which `release-data.yml` publishes |
| `just restore-history prev/warehouse.duckdb` | carry a published release's unreproducible state into this build; refuses if dlt has local state |
| `just bus-matrix` | regenerate the bus matrix block in `docs/WAREHOUSE.md` from the manifest |
| `just disclosure-risk` | reprint the re-identification table from the warehouse |
| `just test` / `just coverage` | `pytest`, mocked, no network; the same with line + branch coverage, gating nothing |
| `just test-pipeline` | the whole pipeline against fixtures, into a throwaway warehouse |
| `just record-fixtures` | re-record `tests/fixtures/ingest/` from the live APIs |
| `just lint` / `just typecheck` | `sqlfluff lint dbt/models dbt/snapshots`; `ty check`, gating nothing |
| `just where` | which warehouse file and landing zone the recipes will use — dbt's log names the *target*, never the file |
| `just sql` | the warehouse in the DuckDB CLI with the lakehouse attached, read-only (`just sql write` to write) |
| `just clean` | delete the gitignored build output (`deep` also drops `reports/node_modules`) |

Always run tools through `uv run` so they use the project venv, with
`--group orchestration` for anything that imports Dagster. dbt commands must run
from the `dbt/` directory (that's where `profiles.yml` lives).

## Style guide

SQL and model conventions are [`docs/STYLE_GUIDE.md`](docs/STYLE_GUIDE.md),
including its tables of deliberate departures from dbt Labs' guides — a
deliberate choice not written down as a departure reads as an oversight. How
sqlfluff (`just lint`), ruff (pre-commit only) and ty (`just typecheck`, gating
nothing) behave is the `linting-and-type-checking` skill. Two ways to damage
files outside that task:

- **Never run a bare `ruff format .`**: unlike the hook it is not scoped, and it
  rewrites the deliberately aligned code blocks in `docs/` and `README.md`.
- **ruff's `--fix` deletes a comment that starts `# noqa`, even when it is
  prose.** Put the rule after the explanation and keep the real directive, with
  its colon, on the code line.

## Dependency and action versions

`.github/dependabot.yml` watches `github-actions`, `uv`, `npm` and
`docker-compose` monthly. What pins what, and why, is the `dependency-versions`
skill. Python is 3.13, set in `.python-version` alone, and three versions can
only age deliberately — `.python-version`, the sqlfluff pair and ruff — because
no watched ecosystem covers them.

## Agent skills

Skills follow the [Agent Skills](https://agentskills.io) standard, so one copy
serves every agent: they live in `.agents/skills/`, which Codex, Gemini CLI,
Copilot and Cursor read, and `.claude/skills` is a symlink to it (`CLAUDE.md`
says what depends on that; whether Cursor and VS Code, which read both paths,
list a skill twice is unmeasured). Vendor skills carry the tool-level knowledge,
and dbt Labs' is the one in use; any agent but Claude Code installs it with
`npx skills add dbt-labs/dbt-agent-skills --global`. **Keep `--global`**: a
project-scope install writes into `.agents/skills/`, which `tests/test_course.py`
globs, holding dbt Labs' skills to this repo's paths.

| Skill | Load it for |
|-------|-------------|
| `adding-a-data-source` | a new source, resource, WDI indicator or raw table, across every layer |
| `querying-the-warehouse` | SQL against the warehouse: read-only connections, the lock, column names |
| `country-stats-models` | the `country_stats` group — OWID, World Bank, Eurostat, the spine |
| `compliance-models` | the `compliance` group — Scope 2 factors and the CBAM annex |
| `retail-models` | the `retail` group — returns inference, cohorts, RFM |
| `currency-and-calendar` | the ECB rates, `dim_date`, spot against average |
| `weather-models` | Open-Meteo's budget, ERA5, the degree-day conventions |
| `contracts-and-data-quality` | data tests, groups, contracts, exposures, additivity, versions |
| `unit-testing-dbt-models` | the twelve unit-tested models and the mutation method |
| `pipeline-observability` | `transform/pipeline_status.py` and the `pipeline_*` tables |
| `the-lakehouse` | the DuckLake catalog |
| `publishing-a-release` | the export, personal data at the boundary, what carries forward |
| `dagster-graph-and-jobs` | backfill windows and partitions, registration, the three jobs |
| `building-evidence-reports` | the Evidence site |
| `linting-and-type-checking` | sqlfluff, ruff and ty |
| `dependency-versions` | what pins what, and the versions nothing watches |
| `repo-guards` | hand-maintained lists, their guards, the fixtures, the suite's traps |
| `authoring-course-modules` | writing `docs/course/` |

Fourteen of the eighteen were split out of this file, because it loads in full
before every session. **A new section here is a question about where it belongs,
not only about what it says**: it stays only if every session needs it — not
knowing it does irreversible damage, gives a silent wrong answer outside any one
domain, or is needed to find everything else. The file accretes in bursts behind
feature work, so check it at the end of anything large, and split in a commit of
its own so the before and after stay measurable with `git show`.
`tests/test_course.py` checks every path and `just` recipe a skill cites, but
**not this file's paths**: `lake/` and `reports/` are both directories and
Dagster asset-key prefixes, so a correct citation of an asset reads as a dead
path.

## Warehouse schemas (`data/lakehouse/` and `data/warehouse.duckdb`)

dlt lands `raw` in the DuckLake catalog; dbt builds everything else into the one
DuckDB file. The full account is [`docs/WAREHOUSE.md`](docs/WAREHOUSE.md).

- `raw` (in the lakehouse) — `owid_co2`, `owid_energy`, `wb_country`, `wb_wdi`,
  `eu_elec_prices`, `ecb_fx_rates`, `retail_invoice_lines`, `om_weather_daily`.
  **`om_weather_daily` cannot be rebuilt** within Open-Meteo's daily allowance,
  so each release carries it forward in `lakehouse.tar.gz`
- `staging` — dbt views, `stg_*`, at `(country_iso3, year)` except `stg_fx_rates`
  `(rate_date, currency_code)`, `stg_retail_lines` `(invoice, line_number)` and
  `stg_weather_daily` `(country_iso3, weather_date)`; `intermediate` — three
  `int_*` views, `private` and uncontracted like staging, and not shipped as
  Parquet
- `marts` — dbt tables, one folder per dbt group:
  - `country_stats/` — `dim_country_year` (the spine), `fct_emissions_energy`
    (**the one versioned model**; `fct_emissions_energy_v1` is a compatibility
    view until 2026-11-01), `fct_co2_estimate_versions`,
    `fct_eu_electricity_prices_semiannual`, `fct_country_weather_year`
  - `reference/` — `dim_country` (**the conformed country dimension**),
    `dim_country_income_history`, `dim_date`, `dim_currency` and the
    `fct_fx_rates_*` models, of which `fct_fx_rates_published` is **the only
    incremental model**
  - `compliance/` — `dim_grid_emission_factors`, `fct_example_scope2_emissions`
    (**the only fabricated data in the warehouse, and it ships**),
    `fct_cbam_exposure` (at `(sourcing country, good)`, **no year at all**)
  - `retail/` — **the only grain below a country**: `fct_retail_order_line`,
    `dim_retail_product`, `dim_retail_customer`, `fct_retail_returns`,
    `fct_retail_customer_cohorts`
- `history` — the snapshots `snap_co2_estimates` and `snap_grid_emission_factors`;
  `analytics` — Polars output, `co2_intensity`, `retail_rfm` and the `pipeline_*`
  tables. **The two snapshots and `analytics.pipeline_runs` are the three tables
  no rebuild can reproduce.**

**"Mart" means the subject area, not the file**: there are four marts, the groups
in `dbt/models/_groups.yml`, and the relations in `marts/` are *mart models*.
`tests/test_documented_counts.py` cannot tell a quotation from an assertion, so
never quote an old count in its old words. Consolidating same-grain models and
moving country attributes off the facts were both measured against — read
[`docs/WAREHOUSE.md`](docs/WAREHOUSE.md) before proposing either.

**The country-year is the dominant grain, not a house rule.** Country facts are
`(country_iso3, year)`, with `region` and `income_group` from
`marts.dim_country`; joining something that isn't a country-year to
`dim_country_year` gives it a fabricated dimension. The exceptions, and how
`fct_emissions_energy` hangs off the spine (a country-year one source reports
still arrives, with nulls elsewhere), are `country-stats-models`.

## Snapshot history (`dbt/snapshots/`)

`snap_co2_estimates` keeps OWID's restatements of published years, which every
other model overwrites (`country-stats-models`); `snap_grid_emission_factors`
keeps the Scope 2 factor's versions from 2015, because a *filed* number has to
stay reconcilable.

- **A snapshot is state, not a build artifact.** It cannot be recomputed, and
  deleting `data/warehouse.duckdb` destroys it — which is why both are narrow.
- **Never test a snapshot change in the real warehouse**: a simulated revision
  stays in the history even after a re-ingest. The recipe, against copies, is in
  `country-stats-models`.
- **The published history is carried, not rebuilt**: every workflow builds from
  an empty file and restores the previous release's `history` first, so anything
  that counts carried rows goes through `restore_history.CARRIED`, never a table
  name (`publishing-a-release`).

## Personal data (`meta: {pii: …}`, `publish/export_warehouse.py`)

One column identifies a person — `dim_retail_customer.customer_id`. It is
classified in the ymls and pseudonymised at the export, never in a model; the
measurements and the mechanism are `publishing-a-release`, and the reasoning is
[`docs/DATA_PROTECTION.md`](docs/DATA_PROTECTION.md). In any task:

- **Deleting the id does not anonymise a customer-grain extract**: 97.4% of
  customers are unique on `net_revenue_gbp` alone.
- **Hash with `||`, never `concat()`**, which skips NULLs and would give every
  anonymous row the same pseudonym.
- **An Evidence source query names its columns**: a `select *` ships every
  column to every visitor.
- **Quote shares, never counts, of anything aggregated over floats** — two
  builds on identical sources disagreed on the number of distinct values.

## Data quality and contracts (`dbt/models/**/_*.yml`)

Data tests, groups and access, contracts, exposures, the additivity labels, the
versioned model and the bus matrix are the `contracts-and-data-quality` skill;
unit tests are `unit-testing-dbt-models`. What bites outside those tasks:

- **`dbt deps` first, always.** `dbt/dbt_packages/` and the manifest are
  gitignored, so a fresh clone needs `dbt deps` before `dbt build`, `dbt parse`
  or `sqlfluff`, and `dbt parse` before the asset graph will load.
- **Test args go under `arguments:`, the key is `data_tests:`, and a yml holds
  one `unit_tests:` key** — a second block parses, and dbt merges the lists with
  only a deprecation warning.
- **Every test's failures are stored**: a red check's rows are in
  `select * from dbt_test__audit.<test_name>`.
- **Contracts are enforced on every mart model**, so a column changing type
  fails the build before it writes. **Never round-trip these ymls through
  PyYAML**: it reflows every description to add a scalar.
- **`fct_emissions_energy_v1`'s deprecation date, 2026-11-01, fails `dbt parse`**
  once it passes — dbt's own default would only warn.
- **Select an asset prefix as `key:"marts/*"`.** A bare `marts/*` materialises
  nothing and exits 0; `just materialize-preview` shows what a selection
  resolves to.

## Pipeline observability (`transform/pipeline_status.py`)

`just pipeline-status` writes `analytics.pipeline_sources`, `pipeline_tables`,
`pipeline_tests` and `pipeline_runs`, and `reports/pages/pipeline.md` renders
them; how each is read, and the traps, are the `pipeline-observability` skill.
**`pipeline_runs` is appended, never replaced, and carried between releases** —
the third table no rebuild can reproduce.

## The lakehouse (`lake/lakehouse.py`)

dlt lands `raw` in a DuckLake catalog under `data/lakehouse/`, and the DuckDB
file holds only what dbt builds. The mechanics are the `the-lakehouse` skill.
What bites outside that task:

- **It is the only copy of every landing table**, so `just clean` never takes it:
  deleting it costs the snapshot lineage and the weather archive, which is days
  of Open-Meteo budget.
- **`just sql` attaches it**, because `staging` and `intermediate` are views over
  `lakehouse.raw`; a bare `duckdb data/warehouse.duckdb` fails them with
  `Catalog "lakehouse" does not exist!`.
- **`LAKEHOUSE_DIR` must be absolute, so `just` exports it for every recipe.**
  DuckLake compares the stored `data_path` as a string, so dlt and dbt spelling
  one directory two ways is refused inside `dbt build`, a layer downstream of the
  cause. `.github/actions/setup` is the one definition of that environment for
  the workflows.
- **`LAKEHOUSE_DATA_PATH` puts the Parquet in an S3-compatible bucket, and
  `LAKEHOUSE_CATALOG` the catalog in Postgres; both outrank `LAKEHOUSE_DIR`.**
  They are `lakehouse.REMOTE_ENV_VARS`, `just where` prints both, the password
  is `PGPASSWORD` and never in the URL, and the release refuses either one
  (`the-lakehouse`).

## Publishing (`publish/export_warehouse.py`)

`just export-data` packages the warehouse into `data/export/`, and
`release-data.yml` publishes it monthly as a dated `data-YYYY-MM-DD` release;
the boundary is `publishing-a-release`. Two things not to need it for:

- **Each release carries the previous one's unreproducible state forward** — the
  snapshots, `analytics.pipeline_runs` and the weather archive. **Only "no
  previous release" may skip the restore**; a failed download or restore is
  fatal, or the next release inherits an empty history.
- **The published file must stay named `warehouse.duckdb`**: dbt writes the
  `intermediate` views fully qualified, so a renamed file breaks them while the
  tables keep working.

## Conventions & gotchas (learned the hard way)

- **Clean schema names** come from `dbt/macros/generate_schema_name.sql`, which
  overrides dbt's default `<target>_<custom>` (which would give `main_marts`).
  Reference marts as `marts.fct_emissions_energy`, not `main_marts.…`.
- **One dbt target, by decision.** A dbt target separates schemas *inside one
  database*; here `WAREHOUSE_PATH` swaps the whole database, and the macro above
  keeps schema names identical everywhere, which is what lets
  `marts.fct_emissions_energy` resolve the same on a laptop, in CI and in the
  release. The reasoning sits in `profiles.yml`; a port to a real warehouse
  should add targets (`docs/REUSING_THIS_STACK.md`).
  - The cost: dbt's one "where am I" line, `Concurrency: 4 threads
    (target='dev')`, names the target and never the file, so a build against the
    real warehouse and one against a course sandbox look the same. `just where`
    prints the file, and every recipe that writes to the warehouse or the landing
    zone takes it as its first dependency — except the three that export
    `WAREHOUSE_PATH` themselves and announce their own.
- **dlt only widens types, and a load is two `run()`s**: the `replace` resources
  with `refresh="drop_resources"`, the `merge` resources without, or the refresh
  drops a merge table and its watermark. A new resource must join
  `FULL_REFRESH_RESOURCES` or `INCREMENTAL_RESOURCES`. How dlt's state, Arrow
  batches and timestamps bite is `adding-a-data-source`.
- **The country-year semantics are the `country-stats-models` skill** — the
  largest body of "plausible number, wrong basis" in the repo. Four facts cannot
  wait for it, because they change what a query *means*:
  - **`income_group` and `region` are today's answer applied to every year.** The
    World Bank publishes only the current classification, so every rollup by
    income group inherits it: 51% of the economies classified in 1990 are in a
    different group today. `marts.dim_country_income_history` holds the
    classification as it stood each year.
  - **Divide by `gdp_constant_usd`, never `gdp_usd`.** `gdp_usd` is *current*
    US$, moving with inflation and the exchange rate; of the 193 countries with
    both series in 2010 and 2024, 30 flip the sign of their decarbonisation trend
    on that choice alone.
  - **"Latest year" is per column, not per table.** Coverage thins unevenly —
    `co2_mt` holds 214 countries where `primary_energy_twh` has 79 — so read
    `reports/sources/warehouse/latest_years.sql`, never a literal. It is the
    latest *observed* year, because `stg_wdi` cuts WDI at `current_date`: the
    World Bank also serves projections.
  - **Eurostat prices are semi-annual.** Chart prices off
    `marts.fct_eu_electricity_prices_semiannual`; the annual column exists to join
    prices to emissions or GDP and is a price nobody paid.
- **Retail reaches the country domain through the `retail_country_map` seed, and
  that join must stay a *left* join**: an inner join deletes the unmapped labels
  before the `relationships` test that exists to catch them can read them
  (`retail-models`).

## Orchestration (`orchestration/`)

Dagster wraps the existing layers rather than replacing them: `ingest`, `dbt`
and `transform` stay independently runnable. Backfill windows, partitions, registration, the three
jobs and the rest are `dagster-graph-and-jobs`. What bites outside it:

- **Asset keys are the join between the layers.** Rename a dbt source table
  without renaming the dlt resource and the graph silently splits in two — both
  halves still run.
- **Everything runs in one process, and one run at a time**, because DuckDB
  takes one writer at a time: `.dagster/dagster.yaml` queues runs, but
  `just materialize` runs outside that queue.
- **`load_retail` runs before `full_refresh`**: dbt reads what it lands, so
  `full_refresh` alone against a fresh warehouse fails in dbt.
- **Every asset and check is listed by hand in `definitions.py`**, and an
  omission is silent — `dagster definitions validate` passes.
- **`orchestration/assets.py` must not use `from __future__ import annotations`**:
  Dagster inspects the `context` parameter's annotation object.

## Testing (`tests/`)

Two tiers, and the split is the point — see [`tests/README.md`](tests/README.md).

- `just test` — mocked-payload unit tests over the Python layers. No network, no
  warehouse.
- `just test-pipeline` — the real modules end to end with `INGEST_FIXTURES=1`,
  every source served from `tests/fixtures/ingest/`, into a throwaway warehouse
  and landing zone. This is what CI runs, so a red PR build means the repo broke,
  not that OWID was down.
- `.github/workflows/nightly.yml` runs the graph against the *live* sources daily
  and opens a `nightly-failure` issue — the signal that the fixtures have drifted.

The suite's own traps (asset-check wiring, CI's re-run set, fixture leaks) are
`repo-guards`, and the mutation method is `unit-testing-dbt-models`. For any
number written into prose or a review:

- **A wall-clock figure drifts, and nothing can guard it.** Date a timing when you
  write it, and after correcting a figure restated across files, `grep` for the
  *old* value and expect a hit. Phrase a pytest count as "pytest cases": the
  counts guard reads a number in front of a bare test noun as a dbt claim.
- **A join is not a census.** To ask how often two models disagree, first count
  the rows only one of them has.
- **A correct number reused for a different claim is a wrong number**, and no
  scanner can see it.
- **A fix that moves no number needs the part of it that does** — when a
  correctness fix is invisible in the data, find the half that can be made to
  show.
- **A test earns its place by mutation**: break the model plausibly against a
  copy of the warehouse and record what moves. A red set is candidates, not a
  verdict.
- **A fixture run leaks through any state it does not override** —
  `WAREHOUSE_PATH`, `LAKEHOUSE_DIR` (and `LAKEHOUSE_DATA_PATH` or
  `LAKEHOUSE_METADATA_SCHEMA`, when set) and dbt's artifact paths. The fixture run
  passes either way; the *next* command against the real warehouse is the one
  that is wrong.

## The course (`docs/course/`)

Ten modules teaching this warehouse, built around the failures that stay green;
00-04 are written. Authoring is the `authoring-course-modules` skill. The course
builds into `data/course/` via `just course-sandbox`, and **`just dbt-build` is
the trap** — it targets the real warehouse, so a drill run through the wrong
recipe writes a deliberately broken model into `data/warehouse.duckdb`.

## Verifying changes

After changing ingestion or models, run the real pipeline (`just run`) and
inspect the warehouse — don't assume. Quick check:

```bash
uv run python -c "import duckdb; \
  print(duckdb.connect('data/warehouse.duckdb', read_only=True).sql(\
  'select * from marts.fct_emissions_energy limit 5'))"
```

## Branches and PRs

Every PR here is **squash-merged**, so `main` is linear with one commit per PR.

- **PR count is a content decision, not a process one.** Only the squashed
  message survives on `main`, so group work by what makes one writable summary
  ("a body of testing plus the defect it uncovered"), not one PR per branch.
- **Stacked PRs need a rebase after the one below merges**: squashing rewrites the
  base's identity, so `git rebase --onto origin/main <old-base> <branch>`.
  **Retarget first, with `gh pr edit <n> --base main`**: merging the PR below with
  `--delete-branch` deletes the stacked PR's base, which closes it, and GitHub will
  not reopen it (#61, 2026-09-16). The flag deletes the local branch too, so
  `<old-base>` has to be a SHA from the stacked branch's log. What
  conflicts is whatever both sides touch — here, the running totals in
  `AGENTS.md` and `docs/DATA_QUALITY.md`. **A derived total written into prose
  behaves like a lock**: no two commits touching it can be reordered or
  cherry-picked independently.
- **`git branch --merged` is useless here**: a squashed commit shares no SHA with
  its branch, and once `main` moves on, a merged branch and one with unique work
  both show a diff. **`git merge-tree --write-tree main <branch>` decides it only
  until `main` touches the branch's files again**: a tree equal to
  `git rev-parse main^{tree}` means the branch adds nothing, but a conflict proves
  nothing — on 2026-09-15 three merged branches, three to five PRs behind, all
  conflicted, one on `CLAUDE.md` alone. **Past that, ask GitHub what it merged**:
  `gh pr list --state merged --head <branch> --json number,headRefOid` gives the
  PR and its final commit, and `git merge-base --is-ancestor <branch> <headRefOid>`
  exiting 0 means every commit on the branch went into it. Look before `-D`.

## Session history

Exported agent session logs go in `docs/sessions/`, which is **gitignored
in full**: transcripts are a local working record, long and duplicating what the
commits say. **Anything learned in a session that should outlive it belongs in
the repo** — in this file if every session needs it, otherwise in the skill for
its area — not in an agent's own memory store, which no other agent, and no
other machine, reads.
