# CLAUDE.md

Guidance for Claude Code (and humans) working in this repo.

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

- **`ingest/` is six source modules plus a coordination layer.** It was split for
  cohesion, not size: no function was oversized, but the names partitioned almost
  perfectly by publisher, with only five shared.
  - The split made one real dependency visible: `weather_locations` reads capital
    coordinates from the World Bank, so `ingest/sources/weather.py` imports
    `worldbank`.
  - **The coordination tuples stay together in `ingest/pipeline.py`** rather than
    becoming per-source metadata, because the comment on `PARTITIONED_RESOURCES`
    argues the rule *comparatively* ("`ecb_fx_rates` merges but is not
    partitioned: its whole series is one request"), which cannot be read if the
    facts live in different files.
  - **Shared helpers are reached as `http.get_json(...)`, never imported by
    name.** `tests/test_ingest.py` monkeypatches them through string literals
    (`setattr(http, "get_json", …)`); a name bound into each source would leave
    those patches pointing at nothing — green tests against the live fetch path.
    There is no re-export facade either, so a stale patch raises
    `AttributeError`.
- **`publish/` is the boundary outward** — the personal-data policy, the storage
  ceiling, attribution. It was carved out of `scripts/` so that
  `orchestration/assets.py` stopped importing the top of its own graph from a
  directory named for helpers, and `pages.yml` triggers on `publish/**` rather
  than `scripts/**`.

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

SQL and model conventions live in [`docs/STYLE_GUIDE.md`](docs/STYLE_GUIDE.md) —
naming, grain, import CTEs, column ordering — with two tables of deliberate
departures: from [dbt Labs' style guide](https://docs.getdbt.com/best-practices/how-we-style/0-how-we-style-our-dbt-projects),
and from [how dbt Labs structure a staging layer](https://docs.getdbt.com/best-practices/how-we-structure/2-staging),
whose five rules this project breaks on all five counts. A deliberate choice not
written down as a departure reads as an oversight. The formatting half is
enforced by [`.sqlfluff`](.sqlfluff) through `just lint`, which is also the
pre-commit hook's entry.

- **sqlfluff is pinned exactly, in one place** (`sqlfluff==4.3.0` in
  `pyproject.toml`). Don't restore the upstream `sqlfluff/sqlfluff` hook: it
  installs its own copy, which drifted (3.3.0 there rejected a window-clause
  `order by` the venv's 4.2.2 accepted, so `just lint` passed and the commit
  failed), and it runs from the repo root, where the dbt templater resolves
  `profiles.yml`'s `../data/warehouse.duckdb` one directory too high.
- **Bump the two sqlfluff lines together.** `sqlfluff-templater-dbt` requires
  `sqlfluff==<its own version>`, so a mismatch fails resolution — loudly, for
  once. The `sqlfluff==` line stays because `just lint` calls `sqlfluff`
  directly.
- **CI lints through `just lint`**, so the linted paths are stated once, and
  `.github/actions/setup` installs `just` for every workflow: a `local` hook whose
  entry is a recipe fails with "Executable `just` not found" without it.

The Python half is ruff, configured in `pyproject.toml` and run only through
pre-commit (`ruff-check` with `--fix`, then `ruff-format`).

- **No `select`: ruff runs its own defaults**, and the exact `rev` in
  `.pre-commit-config.yaml` is what holds them still (0.9 enabled 59 rules; 0.16
  enables 413). ruff is not in the `dev` group, so pre-commit's copy is the only
  one — the mirror image of sqlfluff. `extend-select` re-adds the 18 rules 0.16
  dropped, so a bump cannot silently stop checking star imports and `== None`.
- **`combine-as-imports = true`**, or ruff splits `from x import a, run as b` and
  shreds `orchestration/assets.py`'s imports: four layers each export a `run()`,
  and it aliases every one.
- **`--fix` deletes a comment that starts `# noqa`, even when it is prose.** Put
  the rule after the explanation (`# TRY004 asks for TypeError, but …`) and keep
  the real directive, with its colon, on the code line.
- **0.16 formats Python blocks inside Markdown, so the hook's scope is pinned
  here.** Upstream's `ruff-format` hook added `markdown` to its file types
  between v0.16.0 and v0.16.7 — a patch bump that would have reformatted the
  deliberately aligned code in `docs/` — so `.pre-commit-config.yaml` sets
  `types_or: [python, pyi, jupyter]` itself. A manual `ruff format .` is not
  scoped, and will rewrite blocks in `docs/` and `README.md`.

Types are ty (`just typecheck`). It is **not** in pre-commit or any workflow,
which is the whole shape of the decision.

- **It was chosen over pyright on the install line.** Output was comparable at
  introduction (ty 38 diagnostics in 0.32s, pyright 45 in 4.83s); ty lands in
  `uv.lock` through the `dev` group, while pyright is an unpinned global npm
  binary — the sqlfluff drift again. pyright also could not find `.venv` unaided
  and said nothing, burying real diagnostics under phantom missing imports, which
  is why `[tool.ty.environment].python` is explicit.
- **It is pre-1.0, so `>=` and nothing gates on it.** Diagnostics move between
  patch releases; pin it exactly before putting it in pre-commit or a workflow.
- **Suppressions are inline `# ty: ignore[rule]` beside their reason**, never a
  rules list. There are three: the optional `openpyxl` import in each seed script,
  and `SupportsPipeline.deactivate` in `tests/conftest.py` (declared on
  `Pipeline`, not on the protocol `PipelineContext.pipeline()` returns).
- **The tree is clean, and zero is the point**: a checker that always prints the
  same lines is a checker nobody reads. Getting there showed what noise costs —
  23 of the first 38 diagnostics were `.fetchone()[0]` against aggregates that
  return one row by construction. `modern_data_stack.db` states that invariant
  once (`db.scalar` raises naming the query), and `ingest/http.py`'s
  `get_json_object` narrows `dict | list` once for the sources that only ever
  receive objects.

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

Vendor skills for each layer are declared in [`.claude/settings.json`](.claude/settings.json),
so Claude Code offers to install them when you trust this repo. They carry the
tool-level knowledge; this file and the project skills carry the repo-level
knowledge.

| Plugin | Covers |
|--------|--------|
| `dbt@dbt-agent-marketplace` | [dbt Labs' skills](https://github.com/dbt-labs/dbt-agent-skills) — models, tests, docs, debugging |
| `skill-creator@claude-plugins-official` | authoring and evaluating the project skills below — the one entry about the repo's own tooling rather than a layer of the stack |

Not enabled, but worth knowing about: `dbt-migration@dbt-agent-marketplace`
(one-off dbt Core → Fusion work), `dignified-python@dagster`, and dltHub's
[AI Workbench](https://github.com/dlt-hub/dlthub-ai-workbench)
(`/plugin marketplace add dlt-hub/dlthub-ai-workbench`) — the workbench assumes
its own scaffolding, so prefer the `adding-a-data-source` skill for the pipeline
that already exists here.

**A plugin keeps its place by being used, and use is measured** — by counting
`Skill` invocations across the session transcripts, and checking in `git log`
that the plugin's layer was actually being worked on in the window. Four were
retired on a count of zero: `duckdb-skills` and `astral` (187 transcripts, to
2026-08-27), `dagster-expert` and `polars` (211 transcripts, to 2026-09-02). A
zero count is only evidence for a skill-only plugin; an LSP's use never appears as
a `Skill` call.

- **`dagster-expert` sells the `dg` CLI**, which this project deliberately does
  not install, and `dagster-graph-and-jobs` covers Dagster *in this repo*.
  `duckdb-skills` was the same shape — ad-hoc file querying, S3, spatial joins —
  against `querying-the-warehouse`. **`polars` is the weak call**: nothing
  replaces it, so it is the first to reconsider if `transform/` grows.
- **`astral` could not be reached at all.** It and `ty-lsp` both declare a ty
  language server for `.py`/`.pyi`, the first loaded wins, and `ty-lsp` has to:
  Astral's runs `uvx ty@latest`, the newest ty on every launch, against a
  `just typecheck` that runs `uv.lock`'s — the editor would show findings the
  recipe cannot reproduce. With its server shadowed and its skills unused,
  `astral` was two `[WARN]` lines in the debug log.
  `tests/test_plugin_settings.py` asserts it stays off, with the ordering rule in
  the failure message for whoever re-enables it. Check by hand with
  `claude --debug -p ok` then `grep 'already handled by' ~/.claude/debug/latest`
  — no output is the passing state.
- **Retire a plugin by deleting its entry, never with `false`.** A `false` entry
  reads as a declaration and does nothing; the plugin test fails on one.
- **A retired plugin's `github` marketplace stays registered** (`astral-sh`,
  `dagster`, `polars`) unless the decision is final (`duckdb-skills`). Removing a
  github marketplace *uninstalls* its plugins, and the project declaration does
  not bring them back, because re-registering needs a clone that a
  non-interactive session will not make; the cache survives, so the only symptom
  is plugins quietly missing. A user-level entry for a github marketplace is not a
  duplicate of the project one — leave it.

`.claude/marketplace/` is a repo-local marketplace holding `ty-lsp`, which runs
the dev group's ty as a language server — there is no published ty plugin, and an
LSP server is a ten-line `.lsp.json`. Its command is `uv run ty server`, so it
runs `uv.lock`'s ty and must be launched from the project root. A `directory`
marketplace resolves from a **relative** path (`./.claude/marketplace`) and is
read live from the repo, so editing it needs no reinstall, and removing it from
user settings is safe — the project declaration re-registers it.
`claude plugin marketplace add` writes an *absolute* path into user settings, so
declare it in `.claude/settings.json` by hand.

Project skills in `.claude/skills/` cover the seams the vendor skills can't know:

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

**The country-year is the dominant grain, not a house rule.** Country facts and
staging models are `(country_iso3, year)`, joined on ISO3 + year, with `region`
and `income_group` from `marts.dim_country`. The exceptions are deliberate:
Eurostat prices keep their published `(country_iso3, year, half)` grain (the
annual average is a price nobody paid — see `country-stats-models`),
`fct_cbam_exposure` has no year, and the FX tables have no country. Reaching for
`dim_country_year` when the thing modelled isn't a country-year is how a fact
gets a fabricated dimension.

**The fact hangs off the spine, not off a source.** `dim_country_year` is
`dim_country` × every year the data covers (bounds read from the sources);
`fct_emissions_energy` inner-joins it to the union of country-years any source
reports, then left-joins each source. So:

- A country-year only one source reports still reaches the mart. Expect nulls in
  the columns the others don't cover, and filter charts for what they need.
- The dimension decides *what a country is*: codes it doesn't carry — the World
  Bank's aggregates (`WLD`, `EUU`), Antarctica — cannot reach the mart.
- `max(year)` reports whichever source is furthest ahead, so
  `mart_covers_recent_years` measures each source column separately.
- The spine is the full cross join; left-join a fact onto it to see coverage gaps
  as rows.

## Snapshot history (`dbt/snapshots/`)

`snap_co2_estimates` is an SCD2 snapshot of `stg_co2` (`co2_mt`,
`co2_per_capita`, 1990 onwards, `check` strategy, `hard_deletes='invalidate'`):
OWID restates published years, and every other model overwrites the old number.
`marts.fct_co2_estimate_versions` summarises it and
`reports/pages/restatements.md` renders it. `snap_grid_emission_factors` keeps the
Scope 2 factor's versions from 2015, because a *filed* number has to stay
reconcilable.

- **A snapshot is state, not a build artifact.** It cannot be recomputed, and
  deleting `data/warehouse.duckdb` destroys it — which is why both are narrow.
- **The published history is carried, not rebuilt.** Every workflow builds from
  an empty file, so `release-data.yml` restores the previous release's `history`
  before the graph runs, and `pages.yml` borrows the same file so the Restatements
  page shows real revisions. CI starts empty, so there every row is version 1; the
  page's "nothing revised yet" branch is the honest state, not a broken build.
- **Anything that counts carried rows goes through `restore_history.CARRIED`**,
  never a table name, so a new snapshot is verified as well as carried. `history`
  is carried whole because everything in it is unreproducible; any other schema
  needs a table allowlist.
- **Verify a snapshot change by simulating a revision**, never in the real
  warehouse — the fake version stays in the history even after a re-ingest. With
  `WAREHOUSE_PATH` and `LAKEHOUSE_DIR` pointed at copies: build, `update
  lakehouse.raw.owid_co2 set co2 = co2 * 1.05 where iso_code = 'DEU' and year =
  2019` through `just sql write`, build again, and read
  `fct_co2_estimate_versions`.
- **Evidence cannot write a zero-row source to Parquet** ("too small to be a
  Parquet file"), so `reports/sources/warehouse/co2_estimate_versions.sql`
  selects every country-year and the page filters on `is_revised` itself.

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

One column identifies a person — `dim_retail_customer.customer_id`, UCI's own
pseudonym for a shopper. It is classified in the ymls, pseudonymised at the
publication boundary, and measured rather than asserted. Full reasoning in
[`docs/DATA_PROTECTION.md`](docs/DATA_PROTECTION.md); what it cost to learn:

- **Deleting the id does not anonymise a customer-grain extract.** 98.6% of
  customers are unique on `(first_order_gbp, net_revenue_gbp, n_orders)` with no
  id at all; 97.4% on `net_revenue_gbp` alone. A near-continuous money column at
  person grain is an identifier, which is why `quasi_identifier` is a label with
  no action attached: generalising it would delete the analysis it exists for.
  `just disclosure-risk` reprints the table.
- **Quote shares, never counts, of anything aggregated over floats.** Two
  consecutive builds of `dim_retail_customer` on identical sources gave 5,781 and
  5,785 distinct `net_revenue_gbp` values: float addition is not associative, and
  DuckDB's parallel aggregation fixes no order. Per-row arithmetic, like the
  order-line fact's, is stable.
- **The policy is applied to the published copy, not in a model.** The copy holds
  identifiers no model declares — `dbt_test__audit` tables, and the `staging`
  tables `solidify_staging` materialises from views over `lakehouse.raw`.
  `prepare_copy` solidifies staging *then* pseudonymises; the other order ships
  clear ids in `staging` beside hashed marts, with matching row counts and no
  error.
- **The declared set is expanded by column name across every schema**, because
  copies of the identifier appear where nobody would classify them by hand —
  `dbt_test__audit` tables, and dlt's `raw_staging` merge scratch, a full copy of
  the landing table. The export then verifies what it rewrote against
  `^[0-9a-f]{16}$`, which a five-digit id cannot match.
- **`||`, never `concat()`.** `concat` ignores NULLs, so all 243,007 anonymous
  rows would hash the bare salt onto one pseudonym indistinguishable from a real
  customer. `||` propagates. Pinned in `tests/test_privacy.py`.
- **The salt is required and never defaulted**: the ids run 12346–18287, so the
  whole unsalted rainbow table takes 5 ms to build. The release salt is a stable
  repository secret — a per-run salt would repseudonymise every customer monthly,
  and no consumer could tell a restatement from a re-salting. `just export-data`
  generates a throwaway locally, and even `tests/test_export.py` supplies one.
- **DuckDB cannot enforce access**: `create role`, `grant`, `create user` and
  `create policy` are parser errors in 1.5.5. The boundary is the export, the one
  moment the data leaves the machine it is on.
- **The coverage test is scoped to name collisions.**
  `dim_retail_product.net_revenue_gbp` identifies nobody and
  `dim_retail_customer.net_revenue_gbp` identifies 97.4% — same name, opposite
  answer — so `tests/test_privacy.py` requires a label wherever a name collides
  with a classified one, and nowhere else.
- **A `select *` source query ships every column to every visitor**, and grepping
  the queries for `customer_id` cannot find one that names no columns. The site's
  retail queries select only what their charts draw.
- **Changing a source query's column list needs `just report-clean`**: `just
  report` kept building against a schema that still declared a dropped column
  (`reports/.evidence/template/static/data/…/*.schema.json`). Clear `.evidence/`
  rather than reason about what it reuses.

## Data-quality gates (`dbt/models/**/_*.yml`)

`dbt_utils` is the only dbt package, for four generic tests:
`unique_combination_of_columns` (the grain contracts), `accepted_range`,
`expression_is_true` and `equal_rowcount`. `dbt source freshness` reads dlt's
`_dlt_load_id` as a unix epoch.

- **`dbt deps` first, always.** `dbt/dbt_packages/` and the manifest in
  `dbt/target/` are gitignored, so a fresh clone needs `dbt deps` before
  `dbt build`, `dbt parse` or `sqlfluff`, and `dbt parse` before the asset graph
  will load. The recipes depend on `dbt-deps` and `dbt-parse`; `prepare_if_dev()`
  does both, but only under `dagster dev`.
- **One `unit_tests:` key per yml.** A second block parses and dbt *merges* the
  lists, warning `DuplicateYAMLKeysDeprecation` — gone in Fusion, and silent until
  then.
- **Test args go under `arguments:`, and the key is `data_tests:`.** The flat
  `tests:` form is deprecated in 1.10 and gone in Fusion.
- **Every test's failures are stored**: `+store_failures: true` project-wide, so a
  red check gives `select * from dbt_test__audit.<test_name>` for the rows.
- **Tests are calibrated to fail on bugs, not on reality.** `income_group` is
  nullable on purpose (the `country_overrides` territories have no World Bank
  classification) and `co2_per_capita` has no ceiling (small petrostates reach
  780 t/person). Check the full distribution before tightening a bound — the
  17-country fixture slice passes thresholds the full data breaks.
- **There are thirty-six unit tests, over twelve models, because a data test
  cannot see a wrong answer that is a legal one.** Change `dim_date`'s
  `fiscal_quarter` from `/3 + 1` to `/ 4` and every fiscal quarter is wrong while
  all 19 data tests on the model pass; its three unit tests fail. Which models,
  what mutating each proved, and the fixture shapes are `unit-testing-dbt-models`.
- **A unit test that mocks five inputs is telling you a model is two models.**
  The CBAM fallback rule needed a markup schedule, a country dimension and an
  empty grid table to reach through `fct_cbam_exposure`; against
  `int_cbam_default_factors` it mocks one input. Fixture size is the signal, and
  the case for each of the three `int_*` models.
- **Mutate a determinism guard repeatedly.** DuckDB's parallel asof join picks a
  different tied row per run, so the tie-break test passed a broken model 28.7%
  of the time, and single spot-checks called it stable. `unit-testing-dbt-models`
  has the fixture that brings that to 1.1%.
- **Unit tests stay inside `dbt build`.** dbt Labs' advice to exclude them is
  about warehouse spend; here they cost seconds (~5s of dbt's own time, ~11s wall
  for `just dbt-unit-test`, 2026-09-09), and a broken fiscal calendar should stop
  the release.
- **Source freshness measures our load, not the publisher's.** It is
  tautologically green in CI, so it is a recipe, not a workflow step.

## Contracts, ownership and versions (`_groups.yml`, `_exposures.yml`)

Who owns each model, who may depend on it, what shape it promises, and who reads
it — all declarative, and all enforced by something.

- **Groups are by domain, not layer** — `reference`, `country_stats`,
  `compliance`, `retail` — or no boundary is ever crossed.
- **Staging is `private` and marts are `public`** (set per folder), because every
  mart ships as Parquet to people who cannot be paged. The exceptions are the
  content: `stg_country` and `stg_energy` are `protected`, the only places one
  domain reads another's cleaning layer, with the reasons beside the override.
  Breaking one fails `dbt parse`, naming the consumer.
- **Contracts are enforced on every mart model — 21 relations (20 models, one of
  them versioned) and 407 columns, each with a `data_type`.** The column list was
  generated from `information_schema` and inserted line-wise. **Never round-trip
  these ymls through PyYAML**: it reflows every description to add a scalar.
  - The schema contract catches what the grain contract cannot — a column
    changing type under a consumer. Declaring `year` as `VARCHAR` fails the build
    before it writes anything. CI's 17-country slice builds the same types.
  - A contracted incremental model must set `on_schema_change`;
    `fct_fx_rates_published` uses `fail`, because a new column there needs a
    person to decide on a `--full-refresh` of 265k rows.
- **The marts ymls are one per dbt group**, the split dbt itself can check,
  because shared prose behaves like a merge lock (see *Branches and PRs*).
  `_unit_tests.yml` stays whole: it is one axis of assertion across twelve
  models. A test that names a yml is a list that can go quiet, so the privacy test
  globs and derives the expected set from the `.sql` files. When moving yml
  blocks, compare a manifest fingerprint before and after: a green build proves
  the yml parses, not that nothing moved.
- **Exposures are per *page*** — nine Evidence pages and the release — so
  `dbt ls --select +exposure:evidence_retail` answers "what breaks" for one page.
  `tests/test_exposures.py` holds them to the SQL through
  `publish/build_report.py`'s `page_tables()`. An exposure cannot name Polars
  output, so `pipeline.md` has none (and `index.md` reads nothing); what the pages
  read that dbt cannot describe is exactly `TABLE_TO_ASSET_KEY`, asserted. The
  release exposure is exactly the marts.
- **Every numeric mart column declares `meta: {additivity: …}`** from a closed
  vocabulary — `additive`, `semi_additive`, `non_additive`, `not_a_measure` —
  because neither a type nor a test says whether `sum()` means anything: 118 of
  the 229 are non-additive. Those are manifest counts; `fct_emissions_energy_v1`
  inherits 36 through `include: all`, so the ymls carry 193 literal
  `additivity:` entries. `tests/test_additivity.py` holds coverage, the closed
  vocabulary, numeric-only labels, and a name rule — no ratio-named column may be
  summable — which is the one check that catches a label present and *wrong*.
  - `semi_additive` must say which direction fails, and there are 16
    `semi_additive` columns: `population` gives person-years across years;
    `original_quantity` belongs to the matched purchase, so summing it counts a
    purchase once per return matched to it. `gdp_usd` is `semi_additive` and
    `gdp_constant_usd` `additive` — the constant-dollar gotcha as metadata.
  - The labels ship in `manifest.json`'s `additivity` map (285 columns across 26
    relations), with `analytics`' in `EXTRA_ADDITIVITY` because dbt cannot see
    Polars output. They are stated rather than derived from the mart, because a
    derived label fails *open* when a mart column is renamed.
  - A `meta:` block can sit below a comment or a `description:`, so a line-wise
    insert that only skips comments writes a second `meta:` key — which PyYAML
    silently resolves to the last, and `check-yaml` does not flag.
- **`fct_emissions_energy` is versioned** because nothing in the repo refs it and
  the release ships it: v2 renames `co2_per_gdp` to `co2_kg_per_gdp_ppp_2011`. v2
  is aliased back to the bare relation name, and v1 is a view over v2 that puts
  the old column back last, with its contract declared in the same order.
  - **The `deprecation_date` (2026-11-01) is enforced.** dbt's own behaviour when
    it passes is a warning and exit 0, so `flags.warn_error_options` promotes
    `DeprecatedModel` and `DeprecatedReference` to errors, failing `dbt parse`.
    `UpcomingReferenceDeprecation` stays a warning — it fires during the
    migration window, which is what the window is for — and so does everything
    else: `error: all` would fail the release on a warning some later dbt adds.
  - **Versioning changes the Dagster asset key, silently.** The default
    translator keys a versioned model on its alias alone, dropping the `marts/`
    prefix and with it the model's materialisation history.
    `FolderGroupDbtTranslator.get_asset_key` puts the schema back.
  - **Select a prefix as `key:"marts/*"`.** A bare `marts/*` reads `marts/` as an
    asset key, finds none, takes everything downstream of the empty set,
    materialises nothing and exits 0. `just materialize-preview` shows what a
    selection resolves to first.
- **The bus matrix is derived from the manifest** (`just bus-matrix`, rendered
  into `docs/WAREHOUSE.md`) — business processes down, conformed dimensions
  across, the one thing groups and contracts do not say.
  - A uniqueness test with a `where` is not a grain: read as one,
    `dim_grid_emission_factors` becomes a dimension every country fact
    "conforms" to.
  - Conformance is exact column-name matching, deliberately; an alias list would
    have hidden the `quote_currency`/`currency_code` split it found in the FX
    models.
  - `tests/test_bus_matrix.py` regenerates and compares, and holds the orphan set
    to `KNOWN_UNCONFORMED` both ways.

## Pipeline observability (`transform/pipeline_status.py`)

`just pipeline-status` writes four tables into `analytics`: `pipeline_sources`
(dlt load time, rows and year span per landing table), `pipeline_tables` (the
same per modelled table), `pipeline_tests` (every dbt test, what it guards, and
its failing rows) and `pipeline_runs` (one row per node per dbt invocation, with
timings). `reports/pages/pipeline.md` renders them. The asset
`analytics/pipeline_status` depends on **both** Polars assets, because it
inventories `analytics` and must land after everything it counts.

- **None of it is new instrumentation** — `_dlt_load_id`, `dbt_test__audit` and
  `information_schema` already hold it. The module exists because the SQL is
  dynamic over a table list known only at runtime.
- **Test names come from the manifest**, because dbt truncates and hashes an
  audit-table name longer than 63 characters. The manifest is gitignored, so
  `build_tests` degrades to bare table names without it.
- **A test's verdict is its `fail_calc`, not `count(*)`.** `equal_rowcount`
  returns a one-row summary whether it passed or failed, so counting rows scored
  both as failing against a build that finished ERROR=0. 480 of the 482 tests use
  the default. `severity: warn` carries across as `status='warn'`.
- **An audit table the manifest does not name is stale, and dropped.** dbt never
  removes one, and renaming a model orphans all its tests' tables, which are empty
  and would score as passing. The filter applies only when a manifest is present.
- **It excludes its own output from the inventory**, and must run after
  `dbt build`, which writes the audit schema and the manifest it reads.
- **`pipeline_runs` is a history, so it is appended (`db.append_frame`), never
  replaced**: `run_results.json` holds only the latest invocation. The insert is
  idempotent on `invocation_id`, and a `Carry` rule in `CARRIED`
  (`publish/restore_history.py`) carries it between releases — the first rule to
  name its tables, because the rest of `analytics` is rebuilt every run.
  - No row counts: dbt-duckdb sets `adapter_response.rows_affected` only for
    seeds. `pipeline_tables` measures rows from the warehouse instead.
  - `compile_time_s + execute_time_s` is not `execution_time_s` (57.86s against
    65.14s, measured once): dbt counts work outside both phases, so all three are
    stored.
  - Versioned nodes' ids end `.v1`/`.v2` and test ids end in a hash, so rows are
    labelled through the manifest's `alias` (`observability.node_display_name`),
    and one test goes through `pipeline_status.build_runs` so the wiring is
    covered as well as the resolver.
  - **The reader has to find the artifact.** dagster-dbt gives each invocation a
    unique target directory by default, so every orchestrated build wrote
    `pipeline_runs` with zero rows while `just run` filled it; `dbt.cli(…)` now
    gets `paths.dbt_target_path()`. The only loud symptom was Evidence refusing a
    zero-row Parquet in `pages.yml`. The guard is the blocking
    `run_history_records_this_build` check, which asserts the invocation
    `run_results.json` names is in the table — `count(*) > 0` passes on a
    developer's warehouse that still holds older runs.

## The lakehouse (`lake/lakehouse.py`)

**dlt lands `raw` in a DuckLake catalog under `data/lakehouse/`, and the DuckDB
file holds only what dbt builds.** dbt attaches the catalog (`profiles.yml`'s
`attach:`, `_sources.yml`'s `database: lakehouse`). `just ingest` fills it;
`just lakehouse` *reports* on it. The mechanics are the `the-lakehouse` skill —
the change feed dlt makes useless, reading table versions from the catalog, the
publishing allowlist, the unpinnable extension, migrating an old tree. What bites
outside that task:

- **`just sql` attaches the lakehouse**, because the `staging` views, and the
  `intermediate` views over them, read `lakehouse.raw`; a bare
  `duckdb data/warehouse.duckdb` fails each with `Catalog "lakehouse" does not
  exist!`. It attaches in the warehouse's mode, so only `just sql write` can touch
  a landing table.
- **It is the only copy of every landing table**, so `just clean` never takes it:
  deleting it costs the snapshot lineage and the weather archive, which is days
  of Open-Meteo budget.
- **`LAKEHOUSE_DIR` must be absolute, so `just` exports it for every recipe.**
  DuckLake compares the stored `data_path` with the given one *as strings*, so dlt
  (running from the repo root) and dbt (from `dbt/`) spelling one directory two
  ways is refused inside `dbt build`, a layer downstream of the cause — and no
  recipe reproduces it, because every recipe exports the variable that hides it.
  `WAREHOUSE_PATH` gets away with a relative default because a plain file keeps
  no such record.
- **`.github/actions/setup` is the one definition of that environment** — uv, the
  venv, `just`, and all three paths absolute. `tests/test_workflows.py` requires
  the action to export them, no workflow to set them, and every workflow that
  runs the pipeline to use it.

## Publishing (`publish/export_warehouse.py`)

`just export-data` packages the built warehouse into `data/export/`: a
`COPY FROM DATABASE` copy of the DuckDB file, a zstd Parquet per table in
`staging`/`marts`/`analytics`, `lakehouse.tar.gz`, `manifest.json`,
`SHA256SUMS`, `ATTRIBUTION.md` and the release body. `release-data.yml` runs it
monthly (and on demand) against live sources and publishes a dated
`data-YYYY-MM-DD` release.

- **The published file must be named `warehouse.duckdb` and attached as
  `warehouse`.** DuckDB names a catalog after the file stem, and dbt writes the
  `intermediate` views fully qualified (`warehouse.staging.stg_co2`); rename the
  file or `ATTACH … AS wh` and they raise `Catalog "warehouse" does not exist`
  while the tables keep working. `tests/test_export.py` guards it.
- **Releases redistribute upstream data** under licences — CC BY 4.0, the
  Eurostat and ECB reuse policies, Decision 2011/833 for the CBAM annex — that
  all require attribution. `ATTRIBUTION` is the single source for the shipped file
  and the notes, and `tests/test_export.py` ties it to `ALL_URLS` and to README's
  `## License`.
- **`history` ships in the DuckDB file but not as Parquet**; `raw` is not in the
  file at all, and of the landing tables only `raw.om_weather_daily` ships, in
  `lakehouse.tar.gz`.
- **The rest is the `publishing-a-release` skill.** Three of its results matter
  outside it:
  - The manifest carries `duckdb_version` *and* `storage_version`, because "who
    wrote this" and "can I open it" differ: DuckDB 1.x writes format 64, which
    every client back to v0.10.0 reads. `MAX_PUBLISHED_STORAGE_VERSION` is
    checked against the *toolchain*, so it fires on the Dependabot PR that moves
    DuckDB. Not an upper bound on `duckdb`, deliberately.
  - `lakehouse.tar.gz` has the same kind of ceiling
    (`MAX_PUBLISHED_LAKE_VERSION`), but the DuckLake spec moves when
    extensions.duckdb.org republishes — **there is no PR to fail**, only the next
    CI run.
  - **Each release carries the previous one's unreproducible state forward** —
    the snapshots, `analytics.pipeline_runs`, and the lakehouse with its weather
    archive — so the revision log and the build history span releases and the
    archive keeps deepening instead of cold-starting every month.
    `irreplaceable_rows()` is the one count behind `just clean warehouse`'s gate,
    the restore and the "did not shrink" verify. **Only "no previous release" may
    skip**; a failed download or restore is fatal, or the next release inherits
    an empty history.

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
- **dlt persists its schema and only *widens* types**, so a column that lands with
  the wrong type stays wrong. The pipeline uses `refresh="drop_resources"`
  (`REFRESH` in `ingest/pipeline.py`) to force re-inference; `drop_resources`
  rather than `drop_sources`, because Dagster can run a subset of the source and
  `drop_sources` would wipe the tables that weren't selected.
- **Four resources `replace` and four `merge`, so a load is two `run()`s.**
  `refresh` is an argument to `run()`, not a property of a resource, and it would
  drop a merge table and its watermark. `load_groups()` returns the replace group
  with `REFRESH` and the merge group without, restricted to the resources
  selected. A new resource must join `FULL_REFRESH_RESOURCES` or
  `INCREMENTAL_RESOURCES`; a test asserts the two cover the source.
- **dlt state is keyed on the pipeline *name*, not the destination**, so a
  fixture run would hand its watermarks to the next real run. `build_pipeline()`
  appends `_fixtures` to the name under `INGEST_FIXTURES=1`. (dlt resets state
  when the destination is empty, so this bites only once a real landing zone
  exists.)
- **A resource that yields Arrow gets no `_dlt_load_id` unless you ask** —
  `build_pipeline()` sets `NORMALIZE__PARQUET_NORMALIZER__ADD_DLT_LOAD_ID=true`,
  or the retail table lands with no load provenance and freshness and
  `pipeline_sources` silently skip it. Adding the column to an existing table
  needs a `drop table` plus `refresh="drop_resources"`.
- **`.arrow()` is a streaming reader with a 1,000,000-row default batch**; handed
  to dlt as a table it stored exactly 1,000,000 of 1,067,371 rows, with no error.
  `to_arrow_reader(BATCH)` and `yield from` is the fix, and
  `test_retail_yields_every_row_the_workbook_holds` counts.
- **Declare `timezone: False` on a timestamp column**, or dlt makes it
  `TIMESTAMP WITH TIME ZONE` and a 07:45 till time reads `08:45:00+01:00` on a CET
  machine.
- **The country-year semantics are the `country-stats-models` skill** — the
  largest body of "plausible number, wrong basis" in the repo. Four facts cannot
  wait for it, because they change what a query *means*:
  - **`income_group` and `region` are today's answer applied to every year.** The
    World Bank `/country` endpoint publishes only the current classification, so
    every rollup by income group inherits it. `marts.dim_country_income_history`,
    transcribed from the publisher's own history (`OGHIST.xlsx`, by
    `scripts/build_income_classification_seed.py`), measures the cost:

    | Year | Economies classified | In a different group today |
    |------|---------------------|----------------------------|
    | 1990 | 174 | **89 (51%)** |
    | 2000 | 203 | 102 (50%) |
    | 2010 | 211 | 59 (28%) |
    | 2020 | 212 | 22 (10%) |
    | 2025 | 213 | 0 |

    The current year's 0 is an invariant — the same classification reached two
    ways — so a `warn` test fires when they stop agreeing, which is what a July
    reclassification looks like before anyone re-runs the script. Nothing is
    repointed at the history yet: that is a contract change on every relation
    carrying `income_group`, and a separate decision.
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
- **Adding a WDI indicator is two places** — `WB_WDI_INDICATORS` in
  `ingest/sources/worldbank.py` and a `max(case …)` in `stg_wdi.sql`, held
  together by `tests/test_ingest.py`.
- **Retail reaches the country domain through a seed, and that join must stay a
  *left* join.** `retail_country_map` resolves the source's own country labels
  in `stg_retail_lines`; a join on name would silently lose the nine labels that
  don't match (`EIRE` is the second-largest market). The
  `relationships` test makes an unmapped label from a re-ingest loud — and an
  inner join defeats it, by deleting the unresolved rows before the test reads
  them. The labels and the judgements are the `retail-models` skill.

## Orchestration (`orchestration/`)

Dagster wraps the existing layers; it doesn't replace them. `ingest`, `dbt` and
`transform` stay independently runnable, and `orchestration/assets.py` imports
them rather than duplicating logic (`build_pipeline()`, `dbt build`,
`transform.co2_intensity.run()`).

- **Asset keys are the join between the layers.** dlt resources are keyed
  `raw/<resource>` by `RawSchemaDltTranslator` to match the keys dagster-dbt
  derives from `_sources.yml`. Rename a dbt source table without renaming the dlt
  resource and the graph silently splits in two — both halves still run. Check
  with `dagster definitions validate` and a look at the graph.
- **`orchestration/assets.py` must not use `from __future__ import annotations`.**
  Dagster inspects the `context` parameter's annotation *object*; a stringified
  one fails with a confusing "Cannot annotate `context`".
- **Everything runs in one process** (`in_process_executor`, and the `replace`
  resources in a single op): DuckDB takes one writer at a time, so parallel steps
  would fight over the lock.
- **Three resources are partitioned, under two partition definitions**:
  `raw/wb_wdi` and `raw/om_weather_daily` by year, `raw/retail_invoice_lines` by
  month. `define_asset_job` resolves a selection to a *single* `partitions_def` or
  raises, so `load_retail` carries the retail ingest alone, `full_refresh` is
  everything else but the site, and **`load_retail` runs first**, because dbt
  reads what it lands. `full_refresh` alone against a fresh warehouse fails in dbt
  with `Table with name retail_invoice_lines does not exist!`.
- **Every asset and check is listed by hand in `definitions.py`, and an omission
  is silent** — the asset is simply not in the graph, and `dagster definitions
  validate` passes. `tests/test_definitions.py` compares what `assets.py` defines
  with what the graph resolves.
- **The Evidence site is an asset, excluded from `full_refresh` because it needs
  Node.** `reports/evidence_site` shells out to npm; `ci.yml`, `nightly.yml` and
  `release-data.yml` run `full_refresh` with no Node, and `pages.yml` runs
  `publish_site`. Both selections name what they leave out, so a second
  npm-shaped or differently partitioned asset has to be excluded by hand too.
- **Importing `orchestration.assets` leaves a dlt pipeline active process-wide.**
  The `@dlt_assets` decorators call `build_pipeline()` at import time, so a later
  test calling a resource directly reads the real `~/.dlt` state and fails on
  pagination it never got wrong — only in a full-suite run, only on a machine
  that has loaded WDI. `tests/conftest.py` deactivates it on teardown.
- **The rest is the `dagster-graph-and-jobs` skill** — what earns a partition and
  what a partitioned asset needs to keep working unpartitioned, the traps in the
  registration test, and the costed decisions against `dg` and declarative
  automation. How the site meets the graph is `building-evidence-reports`.
- The `daily_refresh` schedule ships `STOPPED`, so opening the UI does not start
  hammering public APIs. It targets `full_refresh`, so it never builds the site.
- Dagster state lives in `.dagster/` (`DAGSTER_HOME`, exported by the justfile);
  only `dagster.yaml` is checked in.

## Testing (`tests/`)

Two tiers, and the split is the point — see [`tests/README.md`](tests/README.md).

- `just test` — mocked-payload unit tests over the Python layers. No network, no
  warehouse; ~42s for the whole suite (2026-09-11).
- `just coverage` — the same under coverage.py: ~53s, 67% branch / 78% statement
  (2026-09-09). It reports and gates nothing, for ty's reason, and measures the
  mocked tier only, so the transform and lake layers read low.
- `just test-pipeline` — the real modules end to end with `INGEST_FIXTURES=1`,
  every source served from `tests/fixtures/ingest/`, into a throwaway warehouse
  and landing zone. This is what CI runs, so a red PR build means the repo broke,
  not that OWID was down.

**A wall-clock figure in prose drifts, and nothing can guard it.** The pytest
timing was documented at ~1s while the suite grew to ~42s, and its first
correction missed one of the four files that quoted it. Date a timing when you
write it, and after correcting any figure restated across files, `grep` for the
*old* value and expect a hit. Figures that move with the code — coverage
percentages, dbt timing a fixed set of unit tests — hold; the pytest wall clock
does not. Phrase a pytest count as "pytest cases": the counts guard reads a
number in front of a bare test noun as a dbt claim.

- `[tool.coverage.run] source` is a hand-maintained directory list with no
  guard: `publish/` was missing from it, and adding it moved the totals *up*,
  because the blind spot hid well-covered code.
- `coverage run -m pytest`, not `pytest --cov`, which measured identically for
  one more package. `COVERAGE_CORE=sysmon` saves nothing here: the cost is
  imports and DuckDB/dlt work, not line tracing.
- `branch = true`, because the repo argues about deliberately unreachable
  branches in prose, and branch coverage makes them a number.

Gotchas:

- **No routine command evaluates an asset check body.** `just test-pipeline` runs
  the modules in shell order and never executes a job;
  `tests/test_asset_checks.py` calls the bodies directly. **Patching the database
  under a check proves its logic, never its wiring**: a check kept reading the
  warehouse for `raw` after `raw` moved into DuckLake, and its tests passed
  against a fixture that had the table. Assert a count as well as the verdict,
  since the wrong source can get the verdict right by accident.
- **A test file that skips itself in CI's first step runs nowhere unless the
  second names it.** `ci.yml` runs pytest before `dbt parse`, so the five
  manifest-gated files skip there and a later step re-runs them by name;
  `tests/test_workflows.py` compares the two sets both ways. Its detector is
  anchored at column 0, because a guard that reads source as text finds its own
  strings.
- **A join is not a census.** The FX periods staleness gap was sized by comparing
  the period-ends two models share — five rows — and was 22: the daily model stops
  emitting rows for a currency that leaves the ECB panel, which took the worst
  rows out of both sides of the comparison. To ask how often two models disagree,
  first count the rows only one of them has.
- **A fix that moves no number needs the part of it that does.** Restricting
  `fct_cbam_exposure`'s cleanest-source baseline to listed countries changes no
  cell, so only a fixture can hold it; the half that could be made visible — the
  fallback row's own excess, now null — is held by an `expression_is_true`. When a
  correctness fix is invisible in the data, look for the part that can be made to
  show.
- **A test earns its place by mutation**: break the model plausibly against a
  copy of the warehouse, run its data tests, and record what moves. Across seven
  models, 38 mutations, the data tests caught 5. Read a red set as candidates — a
  unit test whose input is mocked `rows: []` goes red on any inner join, guarding
  nothing. The method is in `unit-testing-dbt-models`.
- **A correct number reused for a different claim is a wrong number.** A review
  quoted the 70,174 lines *in* tied groups as the lines the tie-break *dropped*
  (36,656): a real figure with a new meaning, which no scanner can see.
- **A yml `description:` is prose**, and `tests/test_documented_counts.py` scans
  every `dbt/models/**/_*.yml` for test and mart counts. The other numeric claims
  there — row counts, shares — are unguarded, because checking them needs the
  full warehouse, which CI lacks. A stale claim can *move* rather than expire —
  Antarctica's null region left the facts and survives in
  `fct_co2_estimate_versions` — and a figure covering two models is written as
  two numbers, never as a total no model has.
- **Scoring against an external rubric finds counts nothing else counted** —
  `docs/FOR_REVIEWERS.md` §6. It is also where access governance scores
  *Absent*: dbt's `access` governs who may build on a model, not who may read it,
  and DuckDB has no grants.
- **Every hand-maintained list is asserted against the authority it copies** —
  `SOURCE_TABLES`, `RAW_DESCRIPTIONS`, `WB_WDI_INDICATORS`, `ATTRIBUTION`,
  `pages.yml`'s allowlist, the asset-check bodies, the counts in prose. None of
  their failures is loud. What each guard found is in `repo-guards`.
- **A fixture run leaks through any state it does not override.**
  `just test-pipeline` overrides `WAREHOUSE_PATH` (or it overwrites the real
  warehouse with the 17-country slice), `LAKEHOUSE_DIR` (or it merges the slice
  into the real landing zone and its weather archive) and dbt's artifact paths —
  `DBT_TARGET_PATH`, `DBT_MANIFEST_PATH`, `DBT_RUN_RESULTS_PATH` and
  `--target-path` — or the next `just pipeline-status` files the fixture's
  timings in the real build history. `tests/test_workflows.py` holds all four,
  because each is invisible when missing: the fixture run passes and the *next*
  command is the one that is wrong.
- **`WAREHOUSE_PATH` must be absolute**: dbt resolves paths from `dbt/`, the
  Python layers from the repo root.
- **Fixtures filter rows, never columns**, and `fixtures.path_for()` raises on an
  unmapped URL rather than fall back to the network. `_ROUTES` is an ordered
  dispatch table in which a route can be shadowed silently; the checks that close
  it are in `repo-guards`.
- `.github/workflows/nightly.yml` runs the graph against the *live* sources daily
  and opens a `nightly-failure` issue — the signal that the fixtures have drifted.

## The course (`docs/course/`)

Ten modules teaching this warehouse to analytics engineers, built around the
failures that stay green rather than the happy path. Modules 00-04 are written;
05-10 are outlined in `docs/course/README.md`, and `tests/test_course.py` keeps
the material from rotting against the repo it cites.

**Authoring a module is the `authoring-course-modules` skill.** Two things to
know without it: the course builds into `data/course/` via `just course-sandbox`,
and **`just dbt-build` is the trap** — it targets the real warehouse, so a drill
run through the wrong recipe writes a deliberately broken model into
`data/warehouse.duckdb`.

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
  `CLAUDE.md` and `docs/DATA_QUALITY.md`. **A derived total written into prose
  behaves like a lock**: no two commits touching it can be reordered or
  cherry-picked independently.
- **`git branch --merged` is useless here**: a squashed commit shares no SHA with
  its branch. `git diff main..<branch>` being empty is the check; where it is not,
  look before `-D` — a stale branch and one with unique work look the same.

## Session history

Exported Claude Code session logs go in `docs/sessions/`, which is **gitignored
in full**: transcripts are a local working record, long and duplicating what the
commits say. **Anything learned in a session that should outlive it belongs in
this file**, which is the part of that history meant to survive.
