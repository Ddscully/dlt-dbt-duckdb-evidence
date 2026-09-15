# AGENTS.md

Guidance for coding agents (and humans) working in this repo.

## Reading this file

This is the one instructions file for every agent. `CLAUDE.md` imports it and
adds only Claude Code's plugin declarations, and the project skills live in
`.agents/skills/` with `.claude/skills` a symlink to them — Claude Code reads
only its own directory, and Codex only this one.

- **The file is about 70 KB, and Codex reads 32 KiB of it by default.** Codex
  cuts an instructions file at `project_doc_max_bytes` and says so only in a
  trace log, so more than half of this file would be missing with no error.
  The limit is user configuration the repo cannot set: put
  `project_doc_max_bytes = 131072` in `~/.codex/config.toml`.
  `tests/test_agent_instructions.py` keeps this bullet inside the budget.
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
[`docs/REUSING_THIS_STACK.md`](docs/REUSING_THIS_STACK.md): what carries over,
what has to be rewritten, and the decisions that are expensive to change later.
The rest of this file is about *this* warehouse.

**The README is the tour; the reference prose sits in `docs/`, one file per
topic:** [`WAREHOUSE.md`](docs/WAREHOUSE.md) (sources, grains, schemas, the lake),
[`ORCHESTRATION.md`](docs/ORCHESTRATION.md) (the asset graph and its three jobs),
[`DATA_QUALITY.md`](docs/DATA_QUALITY.md) (tests, contracts, groups, exposures,
versions), [`PUBLISHED_DATA.md`](docs/PUBLISHED_DATA.md) (the release and how to
query it), [`DATA_PROTECTION.md`](docs/DATA_PROTECTION.md) (the one personal
column and what the release does to it),
[`DASHBOARD.md`](docs/DASHBOARD.md) (the eleven Evidence pages and the deploy)
and [`FOR_REVIEWERS.md`](docs/FOR_REVIEWERS.md). Those files carry the
*explanation*; this one carries what it cost to learn, and the two should not
duplicate each other. A change to how a layer works usually needs an edit in
`docs/` **and** here.

- **[`PRACTICES.md`](docs/PRACTICES.md) is the README's main entry point** — an
  index over the topics: each practice, the failure it prevents, the number that
  measures it, and where in the code it happens. It restates figures from five
  other files, so a claim added there is a claim to keep in step;
  `tests/test_documented_counts.py` covers its test, mart and additivity counts,
  and nothing covers the rest.
- **[`RUNNING_AS_A_SERVICE.md`](docs/RUNNING_AS_A_SERVICE.md) mostly describes
  what the repo has not built** — an always-on deployment and publish-and-swap
  around the single-writer lock. Its §2 exists as `just serve`; the unit file and
  §4's swap asset do not. Its first paragraph says which is which, and nothing
  else does: `tests/test_course.py` checks cited paths and recipes in the course
  and the skills, never in `docs/`.

Three lessons from building `just serve` apply well beyond it:

- **A design block nobody has executed is prose.** §2's recipe carried two
  defects, both the failure that document is about — a service that looks fine
  and is not. `trap 'kill 0' EXIT` kills the recipe's own shell by SIGTERM, which
  systemd counts as a clean exit, so `Restart=on-failure` never fires; a bare
  `wait` returns only once *every* child has exited, so a dead webserver leaves
  the unit healthy. Neither was visible by reading. The doc's "Stopping it"
  section has the measurements.
- **The DuckDB lock is one writer XOR many readers**, across processes, on the
  pinned 1.5.5: a read-only connection fails while a build holds the file, and a
  build fails while anyone is reading it. The one read that works mid-build is
  `lake.lakehouse.read_only_connection()`, which opens the catalog and never the
  warehouse. The measured table is in `querying-the-warehouse`.
- **`uv sync` strips the venv; `uv run` does not.** `[tool.uv] default-groups` is
  deliberately unset, so a bare `uv sync` installs `dev` alone and removes the
  `orchestration` group — 46 packages including `dagster` and `grpcio`, per
  `uv sync --dry-run` — from under any running service. `uv run` only adds what
  its own groups need (measured 2026-09-11 on uv 0.12.12: a bare `uv run` left
  Dagster installed). This file briefly said the opposite, citing that same dry
  run: **a measurement of one command is not evidence about another.** Widening
  `default-groups` would close the `uv sync` hazard and is deliberately not done;
  the note on that key in `pyproject.toml` says why.

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

The domain-neutral mechanisms live here — `paths`, `fixtures`, `ducklake`,
`observability`, `export`, `history`, `db` — and take their configuration as
arguments. The project modules that call them (`ingest/fixtures.py`,
`lake/lakehouse.py`, `transform/pipeline_status.py`, `publish/export_warehouse.py`,
`publish/restore_history.py`) hold this project's constants and stay the entry
points.

- **`modern_data_stack.paths` is the single answer to "where is the project".**
  Resolution is `PROJECT_ROOT`, then the package's own grandparent when it looks
  like a project, then a marker search up from the cwd — last, because the Dagster
  daemon and the CLI don't necessarily run from the project directory.
  **Exhausting all three raises**, and a cwd fallback must not be added: it would
  resolve the warehouse to `./data/warehouse.duckdb`, which DuckDB then
  *creates*, so an install started outside the tree runs green against an empty
  database. `tests/test_paths.py` pins it.
- **Config reaches a package module as a parameter, never as a constant.**
  Nothing under `src/` knows what a country is; a hardcoded table name there
  undoes the split.
- **A general operation belongs in the general module, even when the duplication
  is small.** `db.write_frames` (register a Polars frame, `create or replace`,
  unregister) first lived in `observability`, so both Polars transforms
  hand-rolled a copy — and both omitted the `unregister`. Its `schema` parameter
  has **no default**: every caller writes `analytics`, which is exactly what
  would make a default invisible to the caller that means something else.
- **`RawSchemaDltTranslator` stays in `orchestration/assets.py`**: moving it would
  put Dagster, an optional dependency group, behind a package import.

## Commands

Use the `justfile` recipes (they map to plain `uv run …` commands):

| Command | What it does |
|---------|--------------|
| `just setup` | `uv sync --group dev --group orchestration`, then `install ducklake` — the extension is a binary from extensions.duckdb.org that no lockfile can name, so it is fetched rather than pinned (DuckDB asks for its own build, so it matches `uv.lock` by construction) |
| `just ingest` | run the dlt pipeline → `raw` in the DuckLake catalog |
| `just ingest-wdi-full` | same, ignoring WDI's incremental watermark (full re-fetch) |
| `just dlt-state` | dlt's incremental state — the WDI watermark and the ECB's last fixing (lives in `~/.dlt`, not the warehouse) |
| `just dbt-deps` | install dbt packages (`dbt_utils`) into `dbt/dbt_packages/` |
| `just dbt-build` | `dbt deps` then `dbt build` (33 models, 2 snapshots, 8 seeds + 482 data tests + 36 unit tests) |
| `just dbt-unit-test` | the dbt unit tests alone — the inner loop for model logic |
| `just dbt-freshness` | `dbt source freshness` — is the warehouse stale? |
| `just dbt-docs` | `dbt docs generate` — renders the metadata layer (columns, contracts, groups, exposures, versions) to `dbt/target/` |
| `just dbt-docs-serve` | the same, then serve it on :8080 |
| `just transform` | Polars derived metrics → `analytics` schema |
| `just pipeline-status` | load times, layer inventory, dbt test state → `analytics.pipeline_*` |
| `just lakehouse` | report what the DuckLake landing zone holds — tables, rows, snapshots |
| `just run` | ingest → dbt-build → transform → pipeline-status (shell ordering) |
| `just dagster` | Dagster UI on :3000 — asset graph, runs, freshness, checks |
| `just materialize` | same pipeline, ordered by the asset graph (`load_retail` then `full_refresh`, no Evidence) |
| `just materialize-site` | the same two jobs + the Evidence site (`publish_site`; needs Node) |
| `just materialize-select 'raw/wb_wdi*'` | one asset + everything downstream (`*` all, `+` one layer) |
| `just materialize-preview '<sel>'` | print what a selection resolves to, materializing nothing — a selection matching zero assets exits 0 |
| `just backfill-wdi 1990 1995` | re-load WDI for one year or a range — the partitioned `raw/wb_wdi` asset |
| `just backfill-weather 2012 2026` | deepen the capital-city weather archive one year at a time — paced against Open-Meteo's budget, so a decade is about an hour and fifteen years is the most one run can hold |
| `just report` / `just report-clean` | build the Evidence site (`--clean` drops the schema cache) |
| `just serve` | run the graph and the dashboard as one always-on service — webserver, daemon and a static file server, no container (`docs/RUNNING_AS_A_SERVICE.md`) |
| `just export-data` | package `data/export/` — the DuckDB copy, Parquet, the lakehouse tarball and checksums that `release-data.yml` publishes |
| `just restore-history prev/warehouse.duckdb` | carry a published release's unreproducible state into this build — `history`, `analytics.pipeline_runs`, and the lakehouse tarball beside the file — refuses if dlt has local state |
| `just bus-matrix` | regenerate the bus matrix block in `docs/WAREHOUSE.md` from the manifest |
| `just disclosure-risk` | reprint the re-identification table from the warehouse |
| `just test` | `pytest` — mocked-payload unit tests, no network |
| `just coverage` | the same with line + branch coverage; reports, gates nothing |
| `just test-pipeline` | the whole pipeline against fixtures, into a throwaway warehouse |
| `just record-fixtures` | re-record `tests/fixtures/ingest/` from the live APIs |
| `just lint` | `sqlfluff lint dbt/models dbt/snapshots` |
| `just typecheck` | `ty check` — Python type diagnostics; reports, gates nothing |
| `just where` | print which warehouse file and landing zone the recipes will use — dbt's own log line names the *target*, never the file |
| `just sql` | open the warehouse in the DuckDB CLI with the lakehouse attached, read-only (`just sql write` to write) |
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

`.github/dependabot.yml` watches three ecosystems — `github-actions` (the
workflows and the composite actions), `uv` and `npm` (`/reports`) — monthly, each
grouped to one PR. **What pins what, and why, is the `dependency-versions`
skill.** Four things not to need it for:

- **Python is 3.13, set in `.python-version` alone.** No workflow passes a
  `python-version`, so that file is what CI, the release and a venv all read.
- **No upper bound is written on a dependency in `pyproject.toml`**; the ceilings
  come from upstream. dagster-dbt caps `dbt-core<1.12` and Python `<3.14`, and
  dagster caps Python `<3.15`.
- **Three versions can only age deliberately** — `.python-version`, the sqlfluff
  pair and ruff — because no watched ecosystem covers them.
- **The `uv` entry is `versioning-strategy: lockfile-only`**, so `pyproject.toml`'s
  bounds stay minimum-supported versions. `npm` stays on the default, because
  there the major *is* the pin.

## Agent skills

Skills follow the [Agent Skills](https://agentskills.io) standard, so one copy
serves every agent. The project's are in `.agents/skills/`, the directory Codex,
Gemini CLI, Copilot and Cursor share; `.claude/skills` is a symlink to it, and
`CLAUDE.md` says what depends on that. Cursor and VS Code read both paths, and
whether they then list a skill twice is unmeasured.

Vendor skills carry the tool-level knowledge; this file and the project skills
carry the repo-level knowledge. The one in use is
[dbt Labs'](https://github.com/dbt-labs/dbt-agent-skills). Claude Code is
offered it from `.claude/settings.json`; any other agent installs it with
`npx skills add dbt-labs/dbt-agent-skills --global`. **Keep `--global`**: a
project-scope install writes into `.agents/skills/` beside the tracked skills,
and `tests/test_course.py` globs that directory, so it would hold dbt Labs'
skills to this repo's paths.

Project skills in `.agents/skills/` cover the seams the vendor skills can't know:

- **`adding-a-data-source`** — the cross-layer workflow (dlt resource → dbt
  source → staging → mart → Dagster asset key → Evidence), including the
  name-matching that silently splits the asset graph if you get it wrong.
- **`querying-the-warehouse`** — read-only connections, the single-writer lock,
  clean schema names, checking `raw` column names before writing SQL.
- **`building-evidence-reports`** — the Evidence layer, which has no vendor skill.
- **`authoring-course-modules`** — writing `docs/course/`: the sandbox recipes,
  the measure-every-number rule, and what `tests/test_course.py` enforces.
- **`compliance-models`** — the Scope 2 factors and the CBAM annex: vintages,
  the fabricated worked example, and the transcription policy.
- **`retail-models`** — the transaction grain: returns inference, cohorts, RFM.
- **`currency-and-calendar`** — the ECB rates, `dim_date`, and spot vs average.
- **`weather-models`** — Open-Meteo's weighted budget, ERA5, the positional
  multi-location response, and the two degree-day conventions.
- **`unit-testing-dbt-models`** — the twelve models that carry unit tests, and
  what mutating each one proved the data tests could not see.
- **`repo-guards`** — the hand-maintained lists, the tests that hold them to the
  tree, and the offline fixture dispatch table.
- **`dependency-versions`** — what pins what, and the three versions nothing
  watches.
- **`country-stats-models`** — the country-year domain: coverage that thins per
  column, current against constant dollars, and the World Bank and Eurostat
  shapes.
- **`the-lakehouse`** — the DuckLake catalog: the change feed dlt destroys, the
  `data_path` that decides portability, and what `lakehouse.tar.gz` may hold.
- **`publishing-a-release`** — the export boundary: the two format ceilings and
  what carries forward between releases.
- **`dagster-graph-and-jobs`** — partitions, registration, the three jobs, and
  the `dg`/declarative-automation decisions.

Eleven of the fifteen were split out of this file: domain or task reasoning that
one session in ten needs, against a file loaded in full before every one. **A new
section here is a question about where it belongs, not only about what it
says.** The file does not drift upward; it accretes in bursts behind feature
work, so check it at the end of anything large rather than on a schedule, and
split in a commit of its own so the before and after stay measurable with
`git show`.

`tests/test_course.py` globs every `SKILL.md`, so each path and `just` recipe a
skill cites is checked, and it checks cross-file markdown anchors across all
tracked markdown. **It does not scan this file's paths**, deliberately: `lake/`
and `reports/` are both directories and Dagster asset-key prefixes, so a correct
citation of the asset `reports/evidence_site` is indistinguishable from a dead
path.

## Warehouse schemas (`data/lakehouse/` and `data/warehouse.duckdb`)

dlt lands `raw` in the DuckLake catalog; dbt builds everything else into the one
DuckDB file.

- `raw` (in the lakehouse) — dlt landing tables: `owid_co2`, `owid_energy`,
  `wb_country`, `wb_wdi`, `eu_elec_prices`, `ecb_fx_rates`,
  `retail_invoice_lines`, `om_weather_daily`. **`om_weather_daily` cannot be
  rebuilt** within Open-Meteo's daily allowance, so each release carries it
  forward in `lakehouse.tar.gz` (see *Publishing*)
- `staging` — dbt views, `stg_*`, cleaned to `(country_iso3, year)` grain —
  except `stg_fx_rates` `(rate_date, currency_code)`, `stg_retail_lines`
  `(invoice, line_number)` and `stg_weather_daily` `(country_iso3, weather_date)`
- `intermediate` — dbt views, `int_*`: three, each earning its place by removing
  a specific cost. `int_country_year_observed` (the country-years the four
  country-stats sources report, derived once instead of twice),
  `int_cbam_default_factors` (Annex I's fallback rule, separately testable) and
  `int_retail_return_matches` (the returns-to-purchase inference). `private` and
  uncontracted, like staging; not shipped as Parquet
- `marts` — dbt tables, one folder per dbt group:
  - `country_stats/` — `dim_country_year` (the spine), `fct_emissions_energy`
    (the wide join on the spine; **the one versioned model**, with
    `fct_emissions_energy_v1` a compatibility view until 2026-11-01),
    `fct_co2_estimate_versions` (revision history, off the snapshot),
    `fct_eu_electricity_prices_semiannual` (Eurostat's half-year grain),
    `fct_country_weather_year`
  - `reference/` — `dim_country` (**the conformed country dimension**, one row
    per `country_iso3`, 228 of them), `dim_country_income_history` (the income
    classification **as it stood in each year** — every other `income_group` is
    today's, stamped on every year), and five with no country in them: `dim_date`,
    `dim_currency`, `fct_fx_rates_published` (the ECB's fixings, and **the only
    incremental model**), `fct_fx_rates_daily` (gap-filled) and
    `fct_fx_rates_periods` (month / quarter / half / year)
  - `compliance/` — `dim_grid_emission_factors` (the Scope 2 reference product),
    `fct_example_scope2_emissions` (the worked example — **the only fabricated
    data in the warehouse**, and it ships), `fct_cbam_exposure` (the CBAM border
    cost, at `(sourcing country, good)` with **no year at all**)
  - `retail/` — **the only grain below a country**: `fct_retail_order_line`
    (`(invoice, line_number)`), `dim_retail_product`, `dim_retail_customer`,
    `fct_retail_returns`, `fct_retail_customer_cohorts`
    (`(cohort_month, months_since_first_order)`)
- `history` — the snapshots `snap_co2_estimates` and `snap_grid_emission_factors`:
  **two of the three tables no rebuild can reproduce**
- `analytics` — Polars output: `co2_intensity` and `retail_rfm`, plus
  `pipeline_sources` / `pipeline_tables` / `pipeline_tests` / `pipeline_runs`.
  **`pipeline_runs` is the third unreproducible table**: it is appended per dbt
  invocation, and the artifact it reads holds only the latest one

**"Mart" means the subject area, not the file.** There are four marts — the
groups in `dbt/models/_groups.yml` — and the 21 relations (20 models, one of them
versioned) in the `marts/` layer are **mart models**. Counting models and calling
them marts is how a stale count once survived two additions to the layer.
`tests/test_documented_counts.py` guards the number, and it cannot tell a
quotation from an assertion, so never quote an old count in its old words.

- **`+group:` is set on the folder** in `dbt_project.yml`, and `+schema: marts` on
  all four, so relation names, the release layout and the asset keys ignore the
  nesting.
- **Consolidating models was measured against.** Pairs that share a grain are
  sparse against each other — `fct_retail_returns` is 18,286 rows against
  `fct_retail_order_line`'s 1,067,371, and `fct_country_weather_year` covers 41
  countries against 228 — so merging means columns null on nearly every row. One
  fact table per business *process*, not per grain.
- **Country attributes stay on the facts.** Normalising `country_name`, `region`
  and `income_group` out to `dim_country` would save 0.4% of
  `fct_emissions_energy`'s Parquet, because zstd dictionary-encodes 228 repeated
  strings to nearly nothing, and no copy can drift, since every one is built from
  the dimension in the same run. It would cost eight pages, two source queries
  and a transform a join each, plus a v3 of the versioned model. Kimball's rule is
  a row-store storage argument.
- **The near-miss is `fct_fx_rates_published`**, a strict subset of
  `fct_fx_rates_daily` (`where is_published_rate`). It stays a mart model as the
  only incremental model and a direct site input, but is arguably an
  intermediate concern.

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

## Domain models with their own skills

Domains with enough hard-won detail load on demand. The table is one row per dbt
group plus the two sources that cut across them, so a group with no skill shows
up as a missing row.

| Domain | Skill | What is in it |
|--------|-------|---------------|
| OWID, the World Bank and Eurostat (the `country_stats` group) | `country-stats-models` | coverage that thins per column, current against constant dollars, territorial against consumption emissions, the WDI window and its restatements, and Eurostat's semi-annual grain |
| Scope 2 factors and CBAM (the dbt `compliance` group) | `compliance-models` | the vintage filter that cannot be a year literal, the fabricated worked example, the annex transcription policy, the 2026/1740 migration, and why Annexes II–IV are left out |
| Retail transactions (the `retail` group) | `retail-models` | the three cleaning decisions whose wrong answers are plausible, the returns inference, the ragged cohort triangle, why `ntile(5)` is wrong for RFM, and the country map that joins retail to the country domain |
| ECB rates and the calendar | `currency-and-calendar` | the 7-day carry-forward cap, spot against average, ISO year against calendar year, and the project's one incremental model |
| Capital-city weather (`om_weather_daily`) | `weather-models` | the weighted rate budget that bounds the whole source, the positional multi-location response, the three-year cold start, and the two degree-day conventions |

The one-liners that must not wait for a skill are in *Warehouse schemas* above
(the fabricated example ships, CBAM has no year, retail is below country grain,
weather cannot be rebuilt) and in *Conventions & gotchas* (four country-stats
facts that change what a query *means*).

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
and `transform` stay independently runnable. Partitions, registration, the three
jobs and the rest are `dagster-graph-and-jobs`. What bites outside it:

- **Asset keys are the join between the layers.** Rename a dbt source table
  without renaming the dlt resource and the graph silently splits in two — both
  halves still run.
- **Everything runs in one process**, because DuckDB takes one writer at a time.
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
  `WAREHOUSE_PATH`, `LAKEHOUSE_DIR` and dbt's artifact paths. The fixture run
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
  base's identity, so `git rebase --onto origin/main <old-base> <branch>`. What
  conflicts is whatever both sides touch — here, the running totals in
  `AGENTS.md` and `docs/DATA_QUALITY.md`. **A derived total written into prose
  behaves like a lock**: no two commits touching it can be reordered or
  cherry-picked independently.
- **`git branch --merged` is useless here**: a squashed commit shares no SHA with
  its branch. `git diff main..<branch>` being empty is the check; where it is not,
  look before `-D` — a stale branch and one with unique work look the same.

## Session history

Exported agent session logs go in `docs/sessions/`, which is **gitignored
in full**: transcripts are a local working record, long and duplicating what the
commits say. **Anything learned in a session that should outlive it belongs in
this file**, which is the part of that history meant to survive — not in an
agent's own memory store, which no other agent, and no other machine, reads.
