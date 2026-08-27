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

**The README is the tour; the reference prose sits in `docs/`.** It was one
1,200-line file until 2026-08-10 and is now split by topic:
[`WAREHOUSE.md`](docs/WAREHOUSE.md) (sources, grains, schemas, the lake),
[`ORCHESTRATION.md`](docs/ORCHESTRATION.md) (the asset graph and its three jobs),
[`DATA_QUALITY.md`](docs/DATA_QUALITY.md) (tests, contracts, groups, exposures,
versions), [`PUBLISHED_DATA.md`](docs/PUBLISHED_DATA.md) (the release and how to
query it), [`DATA_PROTECTION.md`](docs/DATA_PROTECTION.md) (the one personal
column and what the release does to it) and
[`FOR_REVIEWERS.md`](docs/FOR_REVIEWERS.md). Those files carry the
*explanation*; this one carries what it cost to learn, and the two should not
start duplicating each other. A change to how a layer works usually needs an edit
in `docs/` **and** here.

## The package (`src/modern_data_stack/`)

The domain-neutral mechanisms live here — `paths`, `fixtures`, `ducklake`,
`observability`, `export`, `history`, `db` — and take their configuration as
arguments. The project modules that call them (`ingest/fixtures.py`,
`lake/lakehouse.py`, `transform/pipeline_status.py`, `scripts/export_warehouse.py`,
`scripts/restore_history.py`) hold this project's constants and stay the entry
points, so `python -m lake.lakehouse`, the justfile recipes and the asset keys are
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
- **A general operation belongs in the general module, and placement is the
  problem even when the duplication is small.** `db.write_frames` — register a
  Polars frame, `create or replace`, unregister — lived in `observability` as
  `write_status` because that is where it was first needed, so the two Polars
  transforms hand-rolled their own copy rather than import a module about
  dbt/dlt metadata to write a carbon metric. Both copies omitted the
  `unregister` the original does. Its `schema` parameter deliberately has **no
  default**: all three callers write `analytics`, which is exactly what makes a
  default invisible to the fourth caller that means something else, and
  `create or replace` does not ask twice.

- **`RawSchemaDltTranslator` stays in `orchestration/assets.py`** — twenty lines
  around two of that module's constants, and moving it would put Dagster (an
  optional dependency group) behind a package import.

## Commands

Use the `justfile` recipes (they map to plain `uv run …` commands):

| Command | What it does |
|---------|--------------|
| `just setup` | `uv sync --group dev --group orchestration`, then `install ducklake` — the extension is a binary from extensions.duckdb.org that no lockfile can name, so it is fetched rather than pinned (DuckDB asks for its own build, so it matches `uv.lock` by construction) |
| `just ingest` | run the dlt pipeline → `raw` schema in DuckDB |
| `just ingest-wdi-full` | same, ignoring WDI's incremental watermark (full re-fetch) |
| `just dlt-state` | dlt's incremental state — the WDI watermark and the ECB's last fixing (lives in `~/.dlt`, not the warehouse) |
| `just dbt-deps` | install dbt packages (`dbt_utils`) into `dbt/dbt_packages/` |
| `just dbt-build` | `dbt deps` then `dbt build` (28 models, 2 snapshots, 6 seeds + 424 data tests + 27 unit tests) |
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
| `just export-data` | package `data/export/` — the DuckDB copy + Parquet + checksums that `release-data.yml` publishes |
| `just restore-history prev/warehouse.duckdb` | copy the unreproducible tables (`history`, `raw.om_weather_daily`) out of a published release so the build appends to them — refuses if dlt has local state |
| `just test` | `pytest` — mocked-payload unit tests, no network |
| `just coverage` | the same with line + branch coverage; reports, gates nothing |
| `just test-pipeline` | the whole pipeline against fixtures, into a throwaway warehouse |
| `just record-fixtures` | re-record `tests/fixtures/ingest/` from the live APIs |
| `just lint` | `sqlfluff lint dbt/models dbt/snapshots` |
| `just typecheck` | `ty check` — Python type diagnostics; reports, gates nothing |
| `just sql` | open the warehouse in the DuckDB CLI with the lakehouse attached, read-only (`just sql write` to write) |
| `just clean` | delete the gitignored build output (`deep` also drops `reports/node_modules`) |

Always run tools through `uv run` so they use the project venv. dbt commands
must run from the `dbt/` directory (that's where `profiles.yml` lives).

## Style guide

SQL and model conventions live in [`docs/STYLE_GUIDE.md`](docs/STYLE_GUIDE.md) —
naming, grain, import CTEs, column ordering, and where this project deliberately
departs from [dbt Labs' style guide](https://docs.getdbt.com/best-practices/how-we-style/0-how-we-style-our-dbt-projects).
The formatting half of it is enforced by [`.sqlfluff`](.sqlfluff); run
`just lint` (pre-commit runs the same check — literally: the hook is a `local`
one whose entry is `just lint`).

- **sqlfluff is pinned exactly** (`sqlfluff==4.3.0` in `pyproject.toml`) and lives
  in exactly one place. Don't restore the upstream `sqlfluff/sqlfluff` pre-commit
  hook: it installs its own copy, which is how the repo ended up with 3.3.0
  rejecting an `order by` inside a window clause that the venv's 4.2.2 accepted —
  `just lint` passed and the commit hook failed on the same file. It also can't
  run from `dbt/`, so the dbt templater resolves `profiles.yml`'s
  `../data/warehouse.duckdb` one directory too high and dies before linting.
- **Bump the two sqlfluff lines together, or the resolution fails loudly.**
  `sqlfluff-templater-dbt` requires `sqlfluff==<its own version>` — a stable
  upstream property, checked across 3.3.0, 4.0.0, 4.1.0, 4.2.0, 4.2.2 and
  4.3.0 — so the `sqlfluff==` line cannot independently decide anything. Keep it
  anyway: `just lint` invokes `sqlfluff` directly, so it is an honest *direct*
  dependency. This is the one failure mode here that is loud rather than green.
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
- **A comment that starts `# noqa` gets deleted, even in prose.** Explaining a
  suppression on the line above it with `# noqa TRY004: RuntimeError, not
  TypeError, because …` reads as an unused blanket `# noqa` to ruff, and
  `--fix` removes the whole line without a word — the explanation vanished and
  the remaining comment began mid-sentence. Put the rule *after* the prose
  (`# TRY004 asks for TypeError, but …`) and keep the real directive on the code
  line, where `# noqa: TRY004` with the colon is what actually suppresses.
- **The hook id is `ruff-check`.** Plain `ruff` still works but is the legacy
  alias as of 0.12.
- **0.16 formats Python code blocks inside Markdown by default.** The hook is
  scoped to `types_or: [python, pyi, jupyter]` so it never sees `.md` and CI is
  unaffected — but a manual `ruff format .` will rewrite python blocks in `docs/`
  and `README.md`. Evidence pages use `` ```sql `` blocks and are untouched.

Types are ty (`just typecheck`), added 2026-08-24. It is **not** in pre-commit and
**not** in any workflow, which is the whole shape of the decision.

- **It was chosen over pyright on the install line, not the feature list.**
  Measured head to head at introduction: ty 38 diagnostics in 0.32s against
  pyright's 45 errors in 4.83s — close enough that neither wins on output. What
  decided it is that ty installs with `uv add --group dev` and lands in
  `uv.lock`, while pyright wants `npm install -g pyright`, an unpinned global
  Node binary no lockfile here can see. That is the sqlfluff 3.3.0/4.2.2 shape
  exactly, and this repo has already paid for it once.
- **pyright could not find `.venv` unaided and did not say so.** It reported 54
  `reportMissingImports` until pointed at `--pythonpath .venv/bin/python`. A type
  checker that cannot see the environment doesn't fail — it buries the real
  diagnostics under phantom ones. `[tool.ty.environment].python` is set
  explicitly for that reason even though ty found it on its own.
- **It is pre-1.0 (0.0.74) and that governs where it may run.** Diagnostics move
  between patch releases, so a `>=` bound is right *because* nothing gates on it.
  Putting it in pre-commit or a workflow means pinning it exactly first — the
  sqlfluff treatment, for the sqlfluff reason.
- **Suppressions are inline `# ty: ignore[rule]`, never a rules list.**
  `[tool.ty]` deliberately overrides nothing. The one suppression in the tree is
  `scripts/build_cbam_seeds.py`'s `openpyxl` import, which is genuinely optional
  and already carries a `ModuleNotFoundError` branch saying so — the ignore sits
  on that line, next to the reason.
- **The first run found no bug, and the breakdown was the useful part.** 23 of
  the 38 it opened with were one idiom: DuckDB types `fetchone()` as
  `Optional[tuple]`, and this repo wrote `.fetchone()[0]` in 40 places against
  ungrouped aggregates that return exactly one row by construction. That ratio
  was the actual problem — 23 false alarms in a 36-line report is how a checker
  stops being read, and the thirteenth real one then lands in noise nobody
  scans. `modern_data_stack.db` states the invariant once instead; the count is
  **38 → 11**.
- **Stating it bought a real check and found a real annotation defect.**
  `.fetchone()[0]` against a query that unexpectedly returns nothing raises
  `TypeError: 'NoneType' object is not subscriptable` from whichever line
  touched it; `db.scalar` raises naming the query. And
  `observability._period_span` was declared `-> tuple[int, int]` while the
  docstring directly below said "or (None, None) if it has none" — ty pointed at
  the line between them. A checker's value here is regressions and navigation,
  not a backlog.
- **The tree is clean, and getting there took two helpers and two
  suppressions.** The second helper is `_get_json_object` in
  `ingest/pipeline.py`: `_get_json` returns `dict | list` and that union is
  *honest*, because the World Bank really does send `[metadata, [records…]]` and
  both World Bank callers already narrow it by hand — an error object served
  with a 200 is a thing those APIs do. Eurostat's JSON-stat and the ECB's
  `{"rates": …}` are objects, so they say so once instead of at every key. The
  three `columns=` constants are annotated `dict[str, TColumnSchema]`, which is
  dlt's own type for what they already were. The suppressions are
  `SupportsPipeline.deactivate` (declared on `Pipeline`, not on the protocol
  `PipelineContext.pipeline()` is typed to return) and the `openpyxl` import.
- **Zero is the point, not a vanity metric.** A checker nobody runs is worth
  nothing, and one that always prints the same 11 lines is a checker nobody
  runs. It only earns its place in the inner loop if a non-empty report means
  something changed.

## Dependency and action versions

`.github/dependabot.yml` watches three ecosystems — `github-actions` (`/`), `uv`
(`/`) and `npm` (`/reports`) — monthly, each grouped to a single PR. **What pins
what, and why, is the `dependency-versions` skill**: it exists because green CI
proves nothing about versions, and every bullet in it is a way that has already
cost this repo something.

Four things a session should not have to load a skill to know:

- **Python is 3.13, set in one place: `.python-version`.** No workflow passes a
  `python-version` to `setup-uv`, so that file is what CI, the release job and a
  contributor's venv all read.
- **`dagster<3.15` is the only hard upper bound in the tree.** dbt 1.12 and
  Python 3.14 are both blocked upstream rather than here.
- **Three versions can only age deliberately** — `.python-version`, the sqlfluff
  pair and ruff — because no watched ecosystem covers any of them. Somebody has
  to remember instead, and the skill is that somebody.
- **The `uv` entry is `versioning-strategy: lockfile-only`**, so the bounds in
  `pyproject.toml` stay minimum-supported versions rather than pins. `npm` is
  deliberately left on the default, because there the major *is* the pin.
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
| `astral@astral-sh` | [Astral's skills](https://github.com/astral-sh/claude-code-plugins) — uv, ruff and ty, the three tools this repo's Python half is built on |
| `skill-creator@claude-plugins-official` | authoring and evaluating the project skills below — the one entry here that is about the repo's own tooling rather than a layer of the stack |

Not enabled, but worth knowing about: `dbt-migration@dbt-agent-marketplace`
(one-off dbt Core → Fusion work), `dignified-python@dagster`, and dltHub's
[AI Workbench](https://github.com/dlt-hub/dlthub-ai-workbench)
(`/plugin marketplace add dlt-hub/dlthub-ai-workbench`) — the workbench assumes
its own scaffolding, so prefer the `adding-a-data-source` skill below for the
pipeline that already exists here.

`.claude/marketplace/` is a repo-local marketplace, declared in `settings.json`
beside the six vendor ones. It holds `ty-lsp`, which runs the dev group's ty as
a language server — there is no published ty plugin, and an LSP server is a
ten-line `.lsp.json`. Its command is `uv run ty server` rather than a bare `ty`
for the pinning reason above, which means it has to be launched with the project
root as its working directory. A directory marketplace resolves from a
**relative** path (`./.claude/marketplace`) and is read live out of the repo
rather than copied into the plugin cache, so editing the plugin needs no
reinstall. `claude plugin marketplace add` writes an *absolute* path into user
settings, so declare it in `.claude/settings.json` by hand instead.

**The two marketplace kinds behave differently on removal, and the github one
bites.** Removing a `directory` marketplace from user settings is safe — the
project declaration re-registers it on the next session, because the source is
right there in the tree. Removing a `github` one (`astral-sh`) *uninstalls its
plugins*, and the project declaration does **not** silently bring it back: a
re-register needs a clone, which a non-interactive session will not do. The
cache directory survives, so the only symptom is `Total LSP servers loaded: 1`
in the debug log and three skills quietly missing. The user-level entry for a
github marketplace is therefore not duplication of the project one — leave it.

**`ty-lsp` must stay above `astral` in `enabledPlugins`, and the reason is
invisible.** Both declare a language server for `.py`, and the first one loaded
wins — the loser is a `[WARN]` in `~/.claude/debug/latest` that nothing surfaces.
Astral's runs `uvx ty@latest server`, the newest published ty on every launch,
against a `just typecheck` that runs the version in `uv.lock`; ty is 0.0.x and
its diagnostics move between patch releases, so letting theirs win means the
editor showing findings the recipe cannot reproduce. Order in a JSON object is
not a thing anyone expects to matter and `astral` sorts first, so alphabetising
that block — the obvious tidy-up — silently hands `.py` to the unpinned server.
`tests/test_plugin_settings.py` holds it, because JSON has nowhere to put a
comment. Check by hand with `claude --debug -p ok` then
`grep 'already handled by' ~/.claude/debug/latest`.

Their ty skill also says to add an ignore comment only when the user asks for
one. This repo carries two, both with the reason written next to them (see the
ty bullets under *Style guide*); that is a considered disagreement, not drift.

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
- **`unit-testing-dbt-models`** — the eight models that carry unit tests, and
  what mutating each one proved the data tests could not see.
- **`repo-guards`** — the hand-maintained lists, the tests that hold them to the
  tree, and the offline fixture dispatch table.
- **`dependency-versions`** — what pins what, and the three versions nothing
  watches.

Seven of the eleven were split out of this file rather than written fresh: domain
or task reasoning that only one session in ten needs, against a file loaded in
full before every one. Two splits so far — 2026-08-24 (1,805 → 1,151 lines) and
2026-08-27 (2,309 → 1,460) — and the second was needed because the first was
allowed to grow back past its own starting point. **A new section here is a
question about where it belongs, not only about what it says.**

`tests/test_course.py` globs `.claude/skills/*/SKILL.md` rather than listing
them, so every path and `just` recipe a skill cites is checked whether or not
anyone remembers the guard exists. **What it does not check is a markdown
anchor**: `docs/WAREHOUSE.md` linked at `CLAUDE.md#cbam-exposure-…` for three
days after that heading became a skill, green in review and dead on click. That
is now a test too.

## Warehouse schemas (one DuckDB file: `data/warehouse.duckdb`)

- `raw` — dlt landing tables: `owid_co2`, `owid_energy`, `wb_country`, `wb_wdi`,
  `eu_elec_prices`, `ecb_fx_rates`, `retail_invoice_lines`, `om_weather_daily`.
  **`om_weather_daily` is the second table a rebuild cannot reproduce** — not in
  principle, the way a snapshot isn't, but within Open-Meteo's daily allowance,
  which is a weaker claim with the same consequence. **It is carried forward
  like the snapshot now**, and the three guards written for the first one were
  generalised rather than duplicated — see *Publishing* below. The
  `weather-models` skill has the measurement that a carried raw table works at
  all
- `staging` — dbt views, `stg_*`, cleaned to `(country_iso3, year)` grain —
  except `stg_fx_rates`, which is `(rate_date, quote_currency)`,
  `stg_retail_lines`, which is `(invoice, line_number)`, and
  `stg_weather_daily`, which is `(country_iso3, weather_date)`
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
  carried forward by `restore_history` but never verified. Both spots now count
  through `CARRIED` instead. **"It copies the schema, not a table list" was the
  reason to keep it that way, and carrying a *landing* table is what ended it**:
  `history` may be copied whole because everything in it is unreproducible by
  definition, while `raw` holds seven other tables plus dlt's bookkeeping and has
  to be an allowlist. The rule carries its own guard with it — dbt's SCD2 columns
  for a snapshot, dlt's `_dlt_load_id`/`_dlt_id` for a landing table.
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

## Domain models with their own skills

Four domains carry enough hard-won detail to be worth loading on demand rather
than in every session. The models are listed under *Warehouse schemas* above;
the reasoning lives in `.claude/skills/`.

| Domain | Skill | What is in it |
|--------|-------|---------------|
| Scope 2 factors and CBAM (the dbt `compliance` group) | `compliance-models` | the vintage filter that cannot be a year literal, the fabricated worked example, the annex transcription policy, the 2026/1740 migration, and why Annexes II–IV are left out |
| Retail transactions (the `retail` group) | `retail-models` | the three cleaning decisions whose wrong answers are plausible, the returns inference, the ragged cohort triangle, and why `ntile(5)` is wrong for RFM |
| ECB rates and the calendar | `currency-and-calendar` | the 7-day carry-forward cap, spot against average, ISO year against calendar year, and the project's one incremental model |
| Capital-city weather (`om_weather_daily`) | `weather-models` | the weighted rate budget that bounds the whole source, the positional multi-location response, the three-year cold start, and the two degree-day conventions |

The one-liners that must not depend on a skill loading are already in *Warehouse
schemas* above and stay there: `fct_example_scope2_emissions` is the only
fabricated data in the warehouse and it ships in the public release,
`fct_cbam_exposure` has no year in its grain, and the retail models are the only
ones at a grain below a country. Weather's is one line up, in the `raw` bullet:
`om_weather_daily` is the second table a rebuild cannot reproduce, and the three
places that guard the first one have to name it too.

## Personal data (`meta: {pii: …}`, `scripts/export_warehouse.py`)

One column identifies a person — `dim_retail_customer.customer_id`, UCI's own
pseudonym for a shopper. It is classified in the ymls, pseudonymised at the
publication boundary and measured rather than asserted. Full reasoning in
[`docs/DATA_PROTECTION.md`](docs/DATA_PROTECTION.md); what it cost to learn:

- **Deleting the id does not anonymise a customer-grain extract, and the number
  is the argument.** 98.6% of the 5,881 customers are unique on
  `(first_order_gbp, net_revenue_gbp, n_orders)` with no id at all; 97.4% on
  `net_revenue_gbp` alone. **Shares, never counts** — see the float bullet below. A near-continuous money column at person grain is
  an identifier whatever it is called, which is why `quasi_identifier` is a label
  with no action attached: generalising those columns would delete the analysis
  they exist for, so they ship and the page says so. `just disclosure-risk`
  reprints the table from the warehouse.
- **An aggregated float column is not reproducible between builds, which is why
  every figure above is a share and never a count.** Two consecutive
  `dbt run --select dim_retail_customer` against byte-identical sources gave
  5,781 and 5,785 distinct values of `net_revenue_gbp`. It is `sum()` over
  doubles: floating-point addition is not associative and DuckDB's parallel
  aggregation fixes no order, so the last bits of a few hundred customers' revenue
  move per build — and exact equality is what a uniqueness count is made of. The
  share is stable to a tenth of a point. **The lake is unaffected and the boundary
  is worth knowing**: it archives `fct_retail_order_line`, whose money is per-row
  arithmetic rather than an aggregate, so the "byte-identical run to run" property
  there still holds. It is aggregation over floats that is unstable, not floats.
- **The policy is applied to the *copy*, not in a model, and both halves of that
  matter.** `raw` ships inside the published DuckDB file, so a mask in staging
  leaves the original one schema away; and the staging models are **views**, which
  recompute from `raw` in the published copy — mask both and the shipped views
  hash an already-hashed value, so views and marts disagree about who a customer
  is with matching row counts and no error. `export()` grew a `prepare_copy` hook
  for this: base tables are rewritten, views recompute, the two agree.
- **`||`, never `concat()`.** DuckDB's `concat` *ignores* NULLs, so
  `concat(customer_id, salt)` hashes the bare salt on every anonymous row — all
  243,007 of them landing on one pseudonym indistinguishable from a real
  customer. `||` propagates. Pinned in `tests/test_privacy.py`.
- **The salt is required and never defaulted.** The ids run 12346–18287, so the
  complete unsalted rainbow table takes **5 ms** to build. A missing `PII_SALT`
  therefore raises — including for `tests/test_export.py`, whose fixture warehouse
  holds no personal data at all and still has to supply one. The release salt is a
  stable repository secret: a per-run salt would repseudonymise all 5,881
  customers every month, so no consumer could tell a restatement from a
  re-salting. `just export-data` generates a throwaway locally.
- **51 relations carry a `customer_id` and six are declared**, so the policy
  expands the declared set *by column name* across every schema before rewriting.
  Two things live in that gap and neither would ever be classified by hand:
  `raw_staging.retail_invoice_lines` (dlt's merge scratch — a full copy of the
  landing table, 824,364 clear ids, in every release published before this), and
  44 `dbt_test__audit` tables, which are empty only while the tests pass. The
  export then **verifies** what it rewrote against `^[0-9a-f]{16}$`, which is
  decisive rather than heuristic because a five-digit id cannot match it.
- **DuckDB has no access control to enforce any of this** — `create role`,
  `grant`, `create user` and `create policy` are each a *parser error* in 1.5.5,
  not an unsupported feature. There is no user to attach a policy to, so the
  enforcement point cannot be the database and a "restricted" schema would be
  theatre. The boundary is the export, which is the only moment the data crosses
  a machine it is on to a machine it is not.
- **The coverage test is scoped to name collisions, not to every column.**
  `dim_retail_product.net_revenue_gbp` (per product, identifies nobody) and
  `dim_retail_customer.net_revenue_gbp` (per customer, identifies 97.4%) are the
  pair that makes `non_personal` a real label: same name, opposite answer, and
  only a person can say which is which. Labelling all ninety retail columns would
  be paperwork; `tests/test_privacy.py` requires a label only where a name
  collides with a classified one.
- **The site was shipping what no chart drew.**
  `reports/sources/warehouse/retail_rfm.sql` was `select *` — 19 columns
  including the id, the country and three dates, downloaded by every visitor to
  render four. `retail_customers.sql` had picked its columns and kept
  `customer_id` anyway, and `retail_returns.sql` was `select *` too — 23 columns
  to render three, carrying 17,934 clear identifiers. All three are pruned; the
  scatter stays at customer grain because one mark per person is what that chart
  *is*. **A `select *` is invisible to the obvious check**: grepping the source
  queries for `customer_id` cannot find a query that names no columns at all,
  which is how `retail_returns.sql` survived the first pass of this work.
- **Changing a source query's column list needs `just report-clean`.** Evidence
  caches a schema per source (`reports/.evidence/template/static/data/…/*.schema.json`)
  and keys it on the source, not on the query text — so a `just report` after a
  column is dropped builds against a schema that still declares it. Three source
  queries changed shape here, which is exactly the case the recipe's own
  description names.

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
- **One `unit_tests:` key per yml file.** Appending a second block to
  `_unit_tests.yml` parses and runs — dbt *merges* the two lists rather than
  letting the last win — but it warns `DuplicateYAMLKeysDeprecation`, and
  deprecated in 1.10 means gone in Fusion. The tolerant behaviour is the
  dangerous half: nothing is red and nothing is missing, so it survives review.
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
- **There are twenty-seven unit tests, over nine models, and they exist because a data
  test cannot see a wrong answer that is a legal one.** `dim_date`'s
  `fiscal_quarter` carries `accepted_range 1-4`, which is what caught the
  `/3 + 1` float-division bug at quarter *5*. Change the same expression to `/ 4`
  and every fiscal quarter in the warehouse is wrong while **all 19 data tests on
  the model pass** — measured, not argued. Its three unit tests fail on it.
  **Which eight models, what mutating each one proved, and the fixture shapes
  strong enough to catch it are the `unit-testing-dbt-models` skill**, together
  with the mutation method that produced all of it.
- **Unit tests run inside `dbt build`, and they are deliberately left there.**
  dbt Labs recommends excluding them from production runs to save compute; that
  argument is about warehouse spend and this is a local DuckDB build where all
  twenty-seven cost 4.2s. A broken fiscal calendar should stop `release-data.yml`,
  not ride along in it. `just dbt-unit-test` is the ~4s inner loop.
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
    `TABLE_TO_DBT_MODEL` and the release notes all say
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
    everything that spells the key out: `just materialize-select 'key:"marts/*"'`
    stops matching either of them, and Dagster's materialisation history is keyed
    on the asset key, so the model looks like it has never been built.
    `FolderGroupDbtTranslator.get_asset_key` puts the schema back for versioned
    nodes, which is what keeps the version invisible to the rest of the graph.
    Measured against the manifest rather than argued: the default translator
    keys the two nodes `fct_emissions_energy` and `fct_emissions_energy_v1`,
    with no `marts/` on the front; the override keys both under `marts/`.
  - **That selection has to be written `key:"marts/*"`, and it was written
    `marts/*` here from 2026-08-09, which matches nothing at all.** A bare
    prefix is not a glob: the parser reads `marts/` as an asset key, finds none,
    and `*` then takes everything downstream of the empty set — so the recipe
    materialises zero assets and **exits 0**. `key:`, `group:`, `kind:`,
    `sinks(…)` and `roots(…)` all work through the plain `dagster` CLI (the
    selection grammar lives in `dagster` core, not in the `dg` CLI, which this
    project does not install). `dagster asset list --select '<sel>'` is the
    read-only way to see what a selection resolves to before materialising it.

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
  is what dbt does; 422 of the 424 tests use the default. `severity` comes across
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

## The lakehouse (`lake/lakehouse.py`)

**dlt lands `raw` in a DuckLake catalog under `data/lakehouse/`, and the DuckDB
file holds only what dbt builds.** dbt attaches the catalog (`profiles.yml`'s
`attach:`, `_sources.yml`'s `database: lakehouse`) and writes `staging`, `marts`,
`history` into `data/warehouse.duckdb`, which is the whole of what the release
publishes. `just lakehouse` *reports* the catalog; `just ingest` is what fills it.

**The hive archive is gone with it** — `lake/archive.py`, `data/lake/`,
`ARCHIVED_TABLES`, the `lake/parquet_archive` asset and `lake_matches_warehouse`.
It was a second copy of the warehouse written by hand, and DuckLake writes the
same Parquet with a catalog on top. Two of its lessons died with it and are worth
knowing were once true: the archive's output was byte-identical run to run (so a
diff of the *files* was meaningful), and its 275 partitions averaging ~47 kB were
the repo's worked example of partitions far too small for a real lake. Neither
survives a format that content-addresses its files and prunes on statistics.

- **dlt's merge destroys DuckLake's change feed, and this is the finding the
  layer is built around.** `ducklake_table_changes()` returns
  `insert`/`delete`/`update_preimage`/`update_postimage` at row grain and is the
  obvious way to ask what moved. Behind dlt it answers the same thing for
  everything: reloading 500 *identical* rows through `write_disposition="merge"`
  reports **500 preimages and 500 postimages**, because dlt regenerates `_dlt_id`
  *and* `_dlt_load_id` on every row it touches. The feed is faithful; the writer
  is what makes it useless. Measured with three loads — first, identical, one-row
  change — and the second and third are indistinguishable.
  - **The replacement is a snapshot diff, and it is better in two ways.**
    `revisions()` compares the table at two versions with `EXCEPT`, projecting
    dlt's provenance columns away: **0 rows** for the identical reload, **1 row**
    for the one-row change, naming it. It works between *any* two snapshots
    rather than adjacent ones, and it is a query a consumer can re-derive from a
    published catalog months later with no bookkeeping this repo has to keep
    right.
  - **The failure mode is a plausible number, not an exception.** Drop a column
    from the ignore list and every row differs on `_dlt_id` alone, so the diff
    returns the whole table — which reads exactly like a catastrophic upstream
    restatement. `weather_revisions_are_derivable` is bounded on the total for
    that reason, and `tests/test_lakehouse.py` asserts the zero as hard as the
    one.
- **`table_versions` reads the catalog database, because the query surface
  cannot answer the question.** A dlt load writes several snapshots (staging,
  merge, cleanup), so "the previous snapshot" is usually not the previous
  snapshot *of this table*. `snapshots()` returns a `changes` map keyed on table
  *ids*; `table_changes(name, from, to)` raises `Table … does not exist at
  version N` for a range starting before the table did — it needs the answer as
  its argument. DuckLake attaches its own catalog as
  `__ducklake_metadata_<alias>`, so `ducklake_data_file` is readable with no
  second ATTACH (which DuckDB refuses outright: *unique file handle conflict*).
  - **Inlined rows have to be unioned in, and missing them fails silently.**
    DuckLake writes a change of `data_inlining_row_limit` rows or fewer (default
    10) into the catalog rather than to Parquet, leaving no `ducklake_data_file`
    row at all. A version list built from files alone skips that load rather than
    erroring. Not a test-only case: a quiet FX day is under ten rows.
- **`read_parquet` over `data/lakehouse/data/` is not the table**, in either
  configuration. At the default inlining limit small changes live in the catalog
  and the files return superseded values; at 0 they reach Parquet but so does a
  `…-delete.parquet` of `(file_path, pos)`, which makes a glob fail on the schema
  mismatch or — excluded by name — return **both** versions of the row.
- **The catalog stores its `data_path` as given, and that decides portability.**
  Absolute (what the *working* copy holds) means a consumer needs
  `ATTACH … (OVERRIDE_DATA_PATH true)` or a one-row rewrite of
  `ducklake_metadata`; relative is what the published copy holds and what a bare
  `ATTACH` needs. Absolute is right for the working copy because dlt reads it
  from the repo root and dbt from `dbt/`, which no relative path serves; the form
  is rewritten at each boundary (`publish` out, `restore` in).
  - **A relative `data_path` resolves against the catalog *file*, not the process
    working directory** — measured both ways, from the unpacked directory and
    from its parent, and the bare `ATTACH` reads the same rows from either. That
    is stronger than "relocatable" and is what lets the consumer instruction be
    one line with no `cd` in it. `tests/test_export.py` pins it.
- **`just sql` attaches the lakehouse, and without it a third of the warehouse
  does not open.** The nine `staging` models are *views* over `lakehouse.raw`, so
  a bare `duckdb data/warehouse.duckdb` binds the 26 `marts`/`analytics`/`history`
  relations and fails every one of the 9 staging views with `Catalog "lakehouse"
  does not exist!`. This is the `Catalog "warehouse" does not exist` trap from the
  other side, and the export already solves its own half by materialising staging
  (`solidify_staging`) — which is exactly what an interactive session cannot do.
  The recipe attaches in the same mode as the warehouse, so `just sql write` can
  repair a landing table and the default cannot touch one by accident.
- **A tree that predates the move has to be migrated, and nothing says so.** A
  fresh clone cold-starts and a release restore carries the tarball, but a
  working tree that already held `raw` inside `data/warehouse.duckdb` gets
  neither: dbt reads `lakehouse.raw`, the catalog is empty, and the weather
  watermark reads null — so the next ingest cold-starts at
  `WEATHER_COLD_START_YEARS` and *silently* ignores however many years the old
  file holds. Nothing is lost and nothing goes red; the archive is simply in the
  wrong file. Carrying `raw.om_weather_daily` across with its
  `_dlt_load_id`/`_dlt_id` is the whole of the fix, and it is the same shape as
  `restore` — dlt finds no `_dlt_version`, calls the dataset new, and merges onto
  the carried rows.
- **It is the only copy of every landing table, so `just clean` still does not
  take it** — and the reason got stronger. Deleting it costs the snapshot lineage
  *and* the weather archive, which is days of Open-Meteo budget. Both silently.
- **The landing zone is published as a second release asset**, `lakehouse.tar.gz`,
  and that is what keeps the weather archive deepening instead of cold-starting
  every month. `CARRIED` names `history` alone now — the weather rows are not in
  the database to carry — so `scripts/restore_history.py` restores the tarball
  beside it and one command still carries both. Verified end to end: restore a
  release into an empty tree and `weather_watermark()` reads 2022-12-31, so the
  next ingest asks for a 90-day lookback rather than three years.
  - **What ships is an allowlist (`PUBLISHED_TABLES`), for two reasons that would
    each be sufficient.** Cost: only the weather archive is unreproducible within
    the budget; everything else in `raw` is a free re-fetch. Disclosure:
    `raw.retail_invoice_lines` and dlt's `raw_staging` copy hold 824,364 clear
    customer ids between them, and shipping `raw` whole would undo the largest
    privacy gain of the move.
  - **And it cannot be fixed by dropping afterwards, which is why the catalog is
    *built* from the list.** DuckLake keeps dropped tables in earlier snapshots:
    after `drop table`, `select * from lh.raw.secret at (version => 2)` returned
    the row — measured, with a customer id in it. A published catalog contains
    what it was created with, permanently.
  - **The published catalog's `data_path` is relative and the working one's is
    absolute**, and the form is rewritten at each boundary (`publish` out,
    `restore` in). There is no form that serves both: a consumer needs relative
    to open it with a bare `ATTACH` wherever they unpacked it, and the working
    copy needs absolute because dlt reads it from the repo root and dbt from
    `dbt/`. DuckLake checks the stored path on every attach and refuses a
    mismatch, so getting this wrong fails loudly on the *next* command.
  - **`just` exports an absolute `LAKEHOUSE_DIR` for every recipe**, which is not
    a convenience. `profiles.yml`'s relative default made a `dbt build` create a
    catalog recording `../data/lakehouse/data/`, which `lake.lakehouse` then
    could not open — and the error names a path nobody typed. `WAREHOUSE_PATH`
    gets away with a relative default because a plain file keeps no such record.
    - **No workflow goes through `just`, so all four needed the same line, and
      the DuckLake move shipped without it.** They run `uv run dagster job
      execute` directly, and only `ci.yml` set any path at all (`WAREHOUSE_PATH`,
      absolute, for the reason above). dlt writes the catalog from the repo root
      and records an absolute `data_path`; dbt then resolves `profiles.yml`'s
      relative default from `dbt/`, and DuckLake compares the two **as strings**
      — so *the same directory under two spellings* is refused with `DATA_PATH
      parameter "../data/lakehouse/data/" does not match existing data path`.
      The failure is in `dbt build`, one layer downstream of the layer that
      chose the spelling. It cannot be reproduced by any recipe, because every
      recipe exports the variable that hides it: a faithful run has to unset
      `LAKEHOUSE_DIR` and work in a clone, or it proves nothing.
  - **A tarball rather than loose files**, because a GitHub release asset is a
    file and a DuckLake is a directory. One asset also means one line in
    `SHA256SUMS`, so `sha256sum -c` still covers the whole release.
  - **`SHA256SUMS` is now produced by walking the export directory** rather than
    by listing what the code thinks it wrote. The two were identical while the
    release was a database and some Parquet; the first artifact written by a hook
    would have shipped unverified with nothing to say so.
  - **The exporter takes `lakehouse_dir` as a parameter, and defaulting it made a
    test pass for the wrong reason.** `publish_lakehouse` read the module
    constant, so the *shape of the export* depended on whether the machine had
    ingested: an empty `data/lakehouse/` gave five `SHA256SUMS` lines and a
    populated one gave six, and `test_sha256sums_is_checkable` asserted five. It
    was green at commit time because the lakehouse happened to be empty, and it
    would have stayed green in CI forever — CI builds from nothing. It only fails
    on the machine of anyone who has run `just ingest` once. The fixture names an
    empty directory now instead of inheriting one.
  - **The second release asset had no test at all**, which is how the above
    survived. It is a *published artifact*, so it earns three on this repo's own
    terms, and each was confirmed by mutation: the tarball is checksummed (walk
    the directory, not the list); the published catalog opens with a **bare**
    `ATTACH` from any working directory; and a table outside `PUBLISHED_TABLES`
    is absent **at every snapshot**, not merely the current one — which is the
    assertion that matters, because time travel is what makes
    copy-then-drop useless as a mitigation.
  - **The DuckLake extension is the one version in this stack that nothing can
    pin, and it is not the coupling people expect.** It cannot drift against
    `duckdb`: builds are served per DuckDB version and one built for another
    refuses to load, naming both versions. What is unpinnable is the extension
    *itself* — it is a 36 MB binary from extensions.duckdb.org with no PyPI
    package, and `duckdb_extensions()` reports its version as a git hash
    (`d8a1881e`) where `parquet` reports `v1.5.5`. That is the "unpinned binary
    no lockfile here can see" shape that lost pyright to ty and keeps `uvx dg`
    out, except here there is no lockfile-visible alternative to choose.
    - **`just setup` installs it, and that is a download location rather than a
      pin.** DuckDB autoloads it on first use, so the lake works without this;
      what it buys is that a network failure surfaces at setup instead of inside
      a `dbt build` in a Dagster op. The version is right by construction — the
      extension directory is keyed on the DuckDB version, so a bump re-fetches
      rather than going stale, and `duckdb-cli` reads the same directory, so one
      install serves `just sql`. Plain `install` and not `force install`: the
      only case the no-op misses is a rebuild republished for an unchanged
      DuckDB version.
    - **So the catalog's own spec version is guarded instead** —
      `ducklake_metadata` holds `version` (`1.0` today) and `created_by`, the
      direct analogue of `storage_version` and `duckdb_version`, and both now
      ship in `manifest.json` and the release notes. See *Publishing* for the
      shape, which is the storage-version guard's with one part working harder.
  - **dbt's `ATTACH IF NOT EXISTS` leaves an empty DuckDB file** at the catalog
    path on any build that runs before the first ingest, and a read-only attach
    of *that* fails with `Existing DuckLake … does not exist - and creating a new
    DuckLake is explicitly disabled`. So "is there a lakehouse here" is
    `is_catalog()` — does it hold `ducklake_metadata` — not `path.exists()`.
  - **The warm-state refusal moved with the landing zone and is unchanged in
    substance.** dlt with local state against a restored destination still dies
    with `Table with name _dlt_version does not exist!`; re-measured against the
    directory copy rather than inherited from the table carry, because a new
    mechanism does not escape an old trap by being new.


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
- **Releases redistribute upstream data**, which the repo itself doesn't. The
  seven sources are CC BY 4.0 or a Eurostat/ECB reuse policy, and the CBAM
  seeds are EU law under Decision 2011/833 — every one of which permits
  redistribution *on condition of attribution*, so that is the obligation: `ATTRIBUTION` in the export script is
  the single source of truth for both the shipped file and the release notes.
  It said "all four sources" from the initial commit until 2026-08-26, having
  never been touched when FX, retail and CBAM arrived.
  - **It is enforced now** (`tests/test_export.py`), where it used to be an
    instruction here to keep it in step with the README's licence section by
    hand. Sources are tied to `ALL_URLS`; licences are compared against
    README's `## License` in both directions. See *Testing* for what the
    mapping costs and why it cannot be derived.
- **`raw` and `history` ship in the DuckDB file but not as Parquet.** The flat
  files are the modelled layers only (`PUBLISHED_SCHEMAS`); anyone who wants
  dlt's landing tables or the snapshot downloads the database.
- **`data_loaded_at` has to name the catalog, and getting it wrong is silent in
  two directions.** `loaded_at` read an unqualified `raw._dlt_loads` off the
  copy, which stopped being where dlt writes when the landing zone moved into
  DuckLake. On a fresh or CI tree that raises, the `except duckdb.Error` returns
  `None`, and every release body says *"Data last landed: unknown."* On a tree
  migrated in place — still holding the pre-move `raw` — it returns the stale
  copy's timestamp instead: measured 4h20m adrift here, and a believable wrong
  answer is the worse of the two. `transform/pipeline_status.py` was migrated for
  exactly this and documents the hazard; `export.py` was missed, and the test
  fixture hid it by building `raw._dlt_loads` *inside* the warehouse, the shape
  production no longer produces.
  - **The reader is a project hook, not a package parameter.** `export()` takes
    `read_loaded_at` the way it already takes `extra_artifacts` for the tarball,
    so the package still knows nothing about DuckLake; `scripts/export_warehouse.py`
    supplies `landed_at`, which attaches read-only **only when there is a
    catalog** — the same test `solidify_staging` makes, so a warehouse carrying
    its own `raw` is still exportable. Where both exist the catalog wins.
  - **The fixture now holds both tables with different timestamps**, because
    `assert data_loaded_at is not None` passes on a clock read, a stale read and
    the wrong catalog's read — every way of being wrong except the one that
    raises. Asserting the value is what makes the mutation red.
- **The published file's storage format is not its writer's version, and the
  manifest carries both.** `duckdb_version` answers "who wrote this";
  `storage_version` answers "can I open it", which is the only question a
  consumer has. They differ: DuckDB 1.x writes format **64** by default — the
  one `v0.10.0` through `v1.1.3` all read — so a file written by 1.5.5 opens on
  a client five years older. The release notes said "Written by DuckDB 1.5.5.
  Older clients may not read the storage format" for the whole life of the
  release, which was unmeasured and, it turns out, *pessimistic*.
  - **There is no SQL that answers it.** `duckdb_databases()` returns an empty
    `options` map, there is no pragma, and the only surface DuckDB exposes is
    `ATTACH … (STORAGE_VERSION …)` on the write side.
    `modern_data_stack.export.storage_version` reads the header instead — 8-byte
    checksum, `DUCK` magic, little-endian uint64 — which is the right shape for
    a release gate anyway: it describes the artifact, not the process. The
    mapping, measured by writing a file per version on 1.5.5: **64** =
    v0.10.0–v1.1.3, **65** = v1.2.x, **66** = v1.3.x, **67** = v1.4.x, **68** =
    v1.5.x. A *lower* number is the more widely readable file.
  - **The guard is split across two moments because one of them cannot see the
    thing that moves.** DuckDB 2.0 ships a new default storage format, nothing
    caps `duckdb>=1.1`, and `lockfile-only` means the bump arrives as one line
    of a grouped monthly Dependabot PR. Every *artifact* check would pass it —
    they read a file the same binary just wrote. So
    `test_the_installed_duckdb_still_writes_the_format_the_release_promises`
    checks the **toolchain** (a bare `duckdb.connect()`, no `STORAGE_VERSION`)
    and is the only one that fires on the bump, on the PR, before it merges. The
    artifact checks — `export()`'s ceiling and `release-data.yml`'s verify step
    — catch a file that should not be uploaded. This is "green CI proves nothing
    about versions" one turn further round: what moves is the *format of the
    artifact* rather than the code.
  - **`max_storage_version` has no default in the package**, matching `schema`
    on `db.write_frames`. A ceiling is a compatibility promise to consumers the
    package knows nothing about, and it is only meaningful beside the minimum
    reader version a project states — `MAX_PUBLISHED_STORAGE_VERSION` and
    `MIN_READER_VERSION` live in `scripts/export_warehouse.py` together.
  - **The ceiling is tested from both sides**, because `>` against `>=` is the
    slip and a one-sided test passes under either — the same lesson as
    the export ceiling's two sides and the FX partitioning fixture.
  - **Not an upper bound on `duckdb`, deliberately.** Pinning would block every
    unrelated fix in 2.x to guard one property; the tripwire lets the bump land
    and makes a person decide the format question with a red test naming it.
    `dagster<3.15` therefore remains the only hard upper bound in the tree.
- **The landing zone has its own format version, and its tripwire has to work
  harder than the file's.** `ducklake_metadata.version` (`1.0`) is what decides
  whether a consumer's `ducklake` can open `lakehouse.tar.gz`, exactly as
  `storage_version` decides it for `warehouse.duckdb`;
  `MAX_PUBLISHED_LAKE_VERSION` is the ceiling and `ducklake.publish` refuses
  above it. What differs is the *moment*, and it is the whole reason this needed
  building rather than copying.
  - **There is no PR to fail.** DuckDB's format moves when `uv.lock` moves, so
    that tripwire fires on a Dependabot PR with a person already reading the
    diff. The DuckLake spec moves when extensions.duckdb.org republishes, and
    nothing in this repo changes — see the extension bullet under *The
    lakehouse*. `test_the_installed_ducklake_still_writes_the_spec_the_release_promises`
    on the next CI run is the only thing that can say so, which makes the
    toolchain half load-bearing here rather than merely thorough.
  - **A ceiling that exists is not a ceiling that is applied**, and the two need
    separate tests. `test_the_lakehouse_ceiling_is_inclusive` proves `publish`
    enforces a limit it is handed — from both sides, for the `>` against `>=`
    reason — while `test_the_release_path_applies_the_lakehouse_ceiling` drives
    `run()` with an impossible one. Deleting the constant from
    `publish_lakehouse`'s call fails only the second: the manifest assertions
    keep passing, because the spec is still under the ceiling nobody checked.
    Verified by mutation, as was each other half.
  - **`created_by` is a DuckDB git hash and answers a different question.** It
    ships beside the spec for `duckdb_version`'s reason — who wrote this, against
    can I open it — and `catalog_metadata` returns the whole map so the two are
    one read rather than two.
  - dlt's own `automatic_migration` defaults to **False**, so a catalog *we*
    write is safe by refusal if the spec ever moves under us. This guards the
    half dlt cannot see: a consumer meeting a tarball written against a spec
    their extension does not know.
- **Each release carries the previous one's unreproducible tables forward**
  (`scripts/restore_history.py`), which is what makes the published snapshot
  accumulate a real revision log instead of holding one version per row forever,
  and what keeps the weather archive deepening instead of resetting to a
  three-year cold start every month. The restore runs *before* the graph, so dlt
  lands into a file that already exists — safe because dlt keys "is this
  destination fresh?" on its own bookkeeping, not on the file existing (verified:
  a fixture load into a restored file still fetched the full WDI series, not a
  five-year window).
  - **Two reasons a table is carried, and one mechanism.** `history` is state *in
    principle*: no rebuild invents a revision. `raw.om_weather_daily` is
    unreproducible within a *budget* — the archive costs more than Open-Meteo's
    10,000 units a day. Different arguments, identical consequence, so
    `modern_data_stack.history` takes a tuple of `Carry` rules (schema, optional
    table allowlist, the columns that prove the relation qualifies) and `carry`
    has **no default**, for `db.write_frames`'s reason.
  - **The allowlist is not a stylistic choice.** Copying `raw` whole would bring
    dlt's `_dlt_loads`/`_dlt_version`/`_dlt_pipeline_state` with it, and dlt would
    then believe every table the schema describes is present — the *other* merge
    resources die on `DELETE FROM "raw"."ecb_fx_rates" … does not exist`. Measured,
    along with the fact that carrying more bookkeeping makes it worse rather than
    better.
  - **Whether it works at all depends on dlt's *local* state, which is why the
    failure is invisible in CI.** A fresh runner has no `~/.dlt`, so dlt queries
    the destination, finds no `_dlt_version`, treats the dataset as new and merges
    onto the carried rows — measured at 44,936 weather rows surviving a full load.
    A machine that has run `just ingest` has state, trusts it, and dies with
    `Table with name _dlt_version does not exist!`. `sync_destination()` does not
    fix it. So `run()` **refuses** when a landing table would be carried and dlt
    has local state, naming `rm -rf ~/.dlt/pipelines/modern_data_stack` as the
    remedy — the restore is replacing the destination that state describes.
    Carrying `history` alone never creates the `raw` schema and is unaffected,
    which a test pins: the recipe that already worked has to keep working.
  - **`irreplaceable_rows()` is the one count.** `just clean warehouse`'s gate,
    the restore step's `restored_rows` and the "did not shrink" verify all call
    it, so a rule added to `CARRIED` reaches all three at once instead of leaving
    whichever was added last unguarded.
  - **Only "no previous release" may skip.** A failed download or restore is
    fatal in `release-data.yml`: continuing would publish an empty history that
    the *next* release then inherits, which is the exact failure the step
    prevents. The verify step asserts the shipped snapshot is no smaller than
    what was carried in. `pages.yml` runs the same step `continue-on-error`,
    because there the snapshot is a read-only display and a missing release
    should cost one section of one page, not the deploy.
  - **It refuses to overwrite a destination that already holds carried state**,
    so running it against the real warehouse can't destroy months of local
    versions — `--force` if that is genuinely what you want. It also rejects a
    source relation missing the columns its rule requires, which would otherwise
    fail later and much less legibly: a snapshot without dbt's SCD2 columns dies
    inside `dbt build`, and a landing table without `_dlt_load_id`/`_dlt_id` dies
    at the next load with DuckDB's `Adding columns with constraints not yet
    supported`, dlt trying to add the column `NOT NULL` to a table with rows.
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
  2010–2024 and still scored 10% *worse* on carbon intensity.
  `gdp_constant_usd` (`NY.GDP.MKTP.KD`, constant 2015 US$) is the real-terms
  series. **The same failure is now measurable rather than narrated** — see the
  Currency section: the EU household electricity price rose 35% or 13.5% between
  2021-S1 and 2022-S2 depending only on whether you counted in euros or dollars.
  - **The yen figure was wrong here and in `transform/co2_intensity.py` until
    2026-08-24, and it was wrong in the way a plausible number is.** It said the
    yen "fell 28% against the dollar"; 28% is Japan's *current-dollar GDP* fall
    (5.812 → 4.190 tn), i.e. the effect written down as the cause. The yen went
    **87.7 → 151.4 JPY/USD** on ECB annual averages — it lost **42%** of its
    dollar value. The full decomposition, which is worth keeping because it shows
    the currency term dominating: current-$ GDP ×0.721 = real growth ×1.104 ×
    dollar value of the yen ×0.579 × a ×1.128 residual (domestic prices).
  - **"Current US$ is fine for single-year cross-sections" is the shorthand, and
    it means *internally consistent*, not *the same answer*.** Ranking countries
    within income group for 2024 on each basis moves **166 of 194 — 86% — to a
    different rank**, worst move 26 places. Over time it is worse than a ranking
    change: of the 193 countries with both series in 2010 and 2024, **30 flip the
    sign of their decarbonisation trend**, five from improving to worsening
    (Nigeria −13.5% → +76.3%, then Brazil, Japan, Lesotho, Namibia).
- **World Bank WDI** is fetched long (one row per indicator/country/year) and
  pivoted to wide columns in `stg_wdi.sql`. Add indicators in two places:
  `WB_WDI_INDICATORS` in `ingest/pipeline.py` and a `max(case …)` in `stg_wdi.sql`.
  The dict already carries the column name, so those two places restate the
  same mapping — `tests/test_ingest.py` holds them together, including against
  a code pointed at the *wrong* column, which no range test can see.
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
- **This is deliberately not a `dg`-shaped project, and the two halves of that
  decision are separable.** `create-dagster` scaffolds a `defs/` tree that
  autoloads, a `[tool.dg.project]` block and YAML components; `dagster-expert`,
  the vendor skill, is written around the `dg` CLI and assumes all of it. Costed
  2026-08-25 rather than assumed:
  - **The autoloading half is already here and free.** `dagster.components` and
    `dagster.load_from_defs_folder` ship in `dagster` core — no extra package.
    What it would buy is deleting `tests/test_definitions.py`, because an
    unregistered asset becomes impossible rather than merely caught. That trade
    is close to a wash: the test costs ~1s and *also* documents two traps that
    the framework would silently absorb (`get_all_asset_keys()` is too wide;
    `AssetChecksDefinition` subclasses `AssetsDefinition`, so an `isinstance`
    chain in the wrong order measures nothing).
  - **The CLI half is +20 packages on a 151-package tree** — `uv pip install
    --dry-run dagster-dg-cli` installs 24 and removes 4, pulling
    `dagster-cloud-cli`, `github3-py`, `cryptography`, `pyjwt`, `httpx`,
    `questionary` and `yaspin` into a project with no Dagster Plus deployment,
    and forcing dagster 1.13.15 → 1.13.19. That is the harlequin/marimo shape
    exactly — a dev tool that duplicates capability the stack already has is
    weight, and it is measured in the `dependency-versions` skill.
  - **`uvx dg` is the trap, and this repo has already refused it twice.** It
    dodges the lockfile — which is the argument that lost pyright to ty
    ("an unpinned global binary no lockfile here can see") and the reason
    `ty-lsp` runs `uv run ty server` rather than a bare `ty`.
  - **Components would delete the explanation, which is the deliverable.** The
    skill's own dbt page reserves the pythonic `@dbt_assets` path for "complex
    customization" — `FolderGroupDbtTranslator` is exactly that, and its comment
    is longer than its code on purpose.
  - **Declarative automation is the adjacent question, and the answer is the
    same for a different reason: nothing here runs a daemon.** All four
    workflows are one-shot `dagster job execute`; the daemon exists only under
    `just dagster`, locally, to serve the UI. An `AutomationCondition` is
    evaluated by an automation sensor *in the daemon*, so DA here would never
    fire at all — it would restate one legible eight-line `ScheduleDefinition`
    across nine asset definitions and be strictly *less* functional than the
    STOPPED schedule it replaced. Dagster's own decision tree routes "simple,
    fixed time-based execution" to schedules and reserves DA for partition-aware
    and graph-state-dependent triggering; `ScheduleDefinition` raises no
    deprecation warning on 1.13, so this is not a legacy path being tolerated.
    - **The partition angle is the near-miss.** Two assets here *are*
      partitioned, which is DA's stated niche — but backfills are deliberately
      manual (`just backfill-wdi`, "an explicit act with a key you can point
      at"), so DA would automate precisely what this project chose to keep
      explicit.
    - **What would change it is circumstance, not taste**: a long-running daemon
      *and* cadences that diverge — FX is daily, OWID annual, retail a closed
      archive. Today everything moves together on one cron, so there is nothing
      for a condition to express. Note the repo already runs the *observability*
      half of that world: `FreshnessPolicy` on every asset. Heavy use of
      Dagster's modelling with almost none of its runtime is a coherent position
      here, not a half-finished adoption.
  - **The skill's depth and this repo's content are close to disjoint**, which is
    the part worth knowing before reaching for it. Grepping its 172 reference
    files for what `assets.py` actually calls: `FreshnessPolicy` 1 (in a
    components file, dead here), `BackfillPolicy` 0, `end_offset` 0,
    `asset_check` 1 (in passing), against `AutomationCondition` 10 — which this
    project uses nowhere. It is worth loading for **asset selection syntax** and
    the **dagster-dbt/dlt integration pages**, and not for anything else here.

## Testing (`tests/`)

Two tiers, and the split is the point — see [`tests/README.md`](tests/README.md).

- `just test` — mocked-payload unit tests over the ingest/transform logic. No
  network, no warehouse, ~14s for the whole suite. **It said ~1s from the
  initial commit to 2026-08-26**, which was true of a much smaller suite and
  drifted by a factor of fourteen with nothing to notice:
  `tests/test_documented_counts.py` guards counts in front of test-nouns, and a
  *timing* claim has no such guard. Re-measure before quoting one.
  - **Writing that bullet tripped the counts guard, which is worth recording.**
    The first draft said "over 242 &lt;test-noun&gt;" — a *pytest* figure, in a
    document where that noun almost always means a dbt test, so the scanner read
    it as a project-wide dbt claim and failed naming `fct_fx_rates_daily`. The
    guard was right twice over: the number was not one dbt builds, and the
    phrase was genuinely ambiguous to a human reader too. Phrase a pytest count
    as "the whole suite" or "pytest cases", never as a bare number in front of
    that noun.
- `just coverage` — line and branch coverage of that tier, ~18s, at 70% branch /
  73% statement today. Reports and gates nothing (no `fail_under`, not in CI,
  no plugin loaded into `addopts`) for ty's reason. Read it with the two caveats
  in `[tool.coverage.report]`: it measures the mocked tier only, so the
  transform and lake layers read low while `just test-pipeline` exercises them
  end to end, and some of what is uncovered is uncovered deliberately.
  - **It is `coverage run -m pytest`, not `pytest --cov`, and that was
    measured.** `pytest-cov` was added first and dropped the same day: identical
    total, identical runtime to within 0.02s, one more package. Everything under
    `[tool.coverage.*]` is coverage.py's own config and is what *both* read, so
    the wrapper bought the `--cov` flag and nothing else — and a justfile recipe
    hides the two-step regardless. The harlequin/marimo rule (`dependency-versions`)
    applied to a package this repo had just added.
  - **`COVERAGE_CORE=sysmon` is the standard advice for cutting the tax on 3.12+
    and does nothing here** — 18.53s against 18.57s. The cost is coverage's
    startup and reporting, not tracing, because this suite is dominated by
    imports and DuckDB/dlt work rather than by Python line execution. Worth
    knowing before someone reaches for it a second time.
  - **`branch = true` because this repo argues about unreachable branches in
    prose.** Four are documented as deliberately unreachable — `dim_date`'s
    eleven unbuilt fiscal policies, the retail `<> 'adjustment'` clause,
    `period_is_complete`'s boundary and the provably-dead term in
    `co2_intensity_rank_is_dense`. Branch coverage makes them a number.
  - **The first run confirmed a hand-built survey and corrected one item of
    it.** `scripts/build_cbam_seeds.py` and `scripts/measure_disclosure_risk.py`
    are both at 0%; `modern_data_stack/history.py` reads 100%, where a grep for
    test imports had called it uncovered — it is exercised through the
    `scripts/` wrapper. Grep finds importers, not coverage.
  - **`.coverage` needed a `.gitignore` entry.** It is a *required intermediate*
    between `coverage run` and `coverage report`, not an incidental artifact, so
    it is always present after the recipe and `git add -A` would have taken it.
- `just test-pipeline` — the real modules end to end with `INGEST_FIXTURES=1`,
  serving all five sources from `tests/fixtures/ingest/`. This is what CI runs,
  so a red PR build means the repo broke, not that OWID was down.

Gotchas:

- **No routine command evaluates an asset check body, which is how one can read
  the wrong database for a week.** `just test-pipeline` runs the four modules in
  shell order and never calls `dagster job execute`, so it evaluates none of
  them. `tests/test_asset_checks.py` calls them directly, and now runs in CI —
  see the next bullet for why that took correcting. What no test replaces is a
  real materialize: these bodies meet the actual warehouse and catalog only
  there.
- **A test file that skips itself in CI's first step and is not named in its
  second runs *nowhere* in CI, and the skip is the honest-looking half.**
  `ci.yml` runs a bare `uv run pytest` before `dbt deps && dbt parse`, so the
  three files gated on `dbt/target/manifest.json` skip; the step after the parse
  then re-runs them *by name*, and it named only `tests/test_definitions.py`.
  So `test_asset_checks.py` and `test_documented_counts.py` ran on no pull
  request at all — the asset-check bodies and every count cited in the docs —
  and **both files' own headers said CI re-ran them**. The 30 skips were visible
  in every build log and read as normal, because 30 skips *is* normal there.
  `tests/test_workflows.py` compares the gated set against what the workflow
  names, both directions, so a fourth gated file cannot join in silence.
  - **A guard that reads test source as text has to say where it is looking.**
    The first detector searched for `pytestmark` and `manifest_path.exists()`
    anywhere in a file and flagged *itself* — the module writes both strings, in
    the code doing the searching. It is anchored at column 0 now, which is the
    difference between a module-level mark and a mention of one.
  - **Patching the database under a check proves its logic and never its
    wiring**, which is the half that broke: `wdi_indicators_all_present` went on
    reading `data/warehouse.duckdb` after the landing zone moved into DuckLake,
    and both tests kept passing because they handed it a throwaway file that did
    contain `raw.wb_wdi`. On a tree that predates the move the real warehouse
    *also* still holds a stale `raw`, so the check passed against a copy of a
    table that no longer lives there — see the migration bullet under *The
    lakehouse*.
  - **A check's verdict is not enough to assert.** Pointed at the lakehouse, the
    healthy-half test failed loudly and the failing-half test stayed green: it
    asserts a bogus indicator is missing, and it is missing from the real
    catalog too. It asserts the *count* now, which is the assertion that
    notices — the same shape as an export test that passed because the machine
    happened not to have ingested.
- **The way a test here earns its place is mutation**: break the model in a
  plausible way against a *copy* of the warehouse, run its full data-test suite,
  and record the number that moves. "Nothing went red" is the finding, not the
  all-clear — across the seven models mutated this way, 38 mutations were run and
  the data tests caught 5. The method's two traps, and every model's findings,
  are the `unit-testing-dbt-models` skill.
- **Every hand-maintained list here is asserted against the authority it
  copies** — `SOURCE_TABLES`, `RAW_DESCRIPTIONS`, `WB_WDI_INDICATORS`,
  `ATTRIBUTION`, `pages.yml`'s path allowlist, the seven `@dg.asset_check`
  bodies, and every count cited in prose. Not one of those failures is loud: an
  unlisted source yields no row and the page under-reports while looking
  complete, a stale count reads as authoritative, an unregistered check simply
  never runs. What each guard found, and the mutation that proved each one
  actually looks, are the `repo-guards` skill.
- **`WAREHOUSE_PATH` overrides the DuckDB file** for `ingest`, `transform`, `lake`
  *and* dbt's profile. It must be **absolute**: dbt resolves its path from `dbt/`,
  the Python layers from the repo root. `just test-pipeline` sets it to a temp file
  — without that, a fixture run overwrites the real warehouse with the 17-country
  slice. `LAKEHOUSE_DIR` is the same idea for `data/lakehouse/`, and the recipe
  sets both — but that one is not an optimisation. dlt *lands* in the
  lakehouse, so without the override a fixture run merges the 17-country slice
  into the real landing zone, over a weather archive no rebuild can afford.
- **Fixtures filter rows, never columns**, and `fixtures.path_for()` raises on an
  unmapped URL rather than falling back to the network — otherwise "offline CI"
  quietly becomes "CI that's online sometimes". `_ROUTES` is an *ordered*
  dispatch table, so a route can be shadowed in silence; the four checks that
  close that loop, the three fixtures that aren't trimmed and the content-keyed
  retail workbook cache are all in the `repo-guards` skill.
- `.github/workflows/nightly.yml` runs the same graph against the *live* sources
  daily and opens (or comments on) a `nightly-failure` issue. That's the signal
  that the fixtures have drifted from reality.

## The course (`docs/course/`)

Ten modules teaching this warehouse as training material for analytics
engineers, built around the failures that stay green rather than the happy path.
Modules 00-04 are written and set the format; 05-10 are outlined in
`docs/course/README.md`, and `tests/test_course.py` stops the material rotting
against the repo it cites.

**Authoring a module is the `authoring-course-modules` skill.** It carries the
sandbox recipes, the rule that every number in the material is measured, what
the structural guard enforces, and the findings the drills produced. Two things
worth knowing without loading it: the course builds into `data/course/` via
`just course-sandbox`, and **`just dbt-build` is the trap** — it targets the real
warehouse, so a drill run through the wrong recipe writes a deliberately broken
model into `data/warehouse.duckdb`.

## Verifying changes

After changing ingestion or models, run the real pipeline (`just run`) and
inspect the warehouse — don't assume. Quick check:

```bash
uv run python -c "import duckdb; \
  print(duckdb.connect('data/warehouse.duckdb', read_only=True).sql(\
  'select * from marts.fct_emissions_energy limit 5'))"
```

## Branches and PRs

Every PR here is **squash-merged**, so `main` is linear and one commit per PR —
`git log --merges main` is empty. That is a setting with consequences worth
knowing before stacking work.

- **A PR is a commit on `main`, so PR count is a content decision, not a
  process one.** Eight commits over two PRs squash to two messages; the eight
  individual messages survive only on the PR pages. Group by what makes one
  writable summary — "a body of testing plus the defect it uncovered" worked
  twice — rather than one per branch.
- **Stacked PRs need a rebase after the one below merges, and the conflicts are
  predictable.** Squashing rewrites the base's identity, so the child is rebased
  onto commits it has never seen: `git rebase --onto origin/main <old-base>
  <branch>`. Every extra level in the stack is one more of those.
  - What conflicts is whatever both sides touch, which in this repo means the
    running totals in `CLAUDE.md` and `docs/DATA_QUALITY.md`. **A derived total
    written into prose behaves like a lock** — no two commits touching it can be
    reordered or cherry-picked independently. The `_unit_tests.yml` additions
    barely conflict at all, being appends to different blocks; it is the
    one-line summary above them that welds a stack into a fixed order.
- **`git branch --merged` is useless here.** The squashed commit shares no SHA
  with the branch, so five fully-merged branches reported as unmerged and
  `git branch -d` refuses them. The check that works is `git diff main..<branch>`
  being empty. Where it is *not* empty, look before deleting: a stale branch and
  a branch with unique work look the same to `-D`.

## Session history

Exported Claude Code session logs go in `docs/sessions/`, which is **gitignored
in full** — nothing there is in the repo, so a fresh clone has no such directory.
Transcripts are a local working record, not project history: they're long, they
duplicate what the commits already say, and while the directory held both tracked
and untracked files a `git add -A` could commit scratch notes that were never
meant to ship. **Anything learned in a session that should outlive it belongs in
this file**, which is the part of that history meant to survive.
