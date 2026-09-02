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
column and what the release does to it),
[`DASHBOARD.md`](docs/DASHBOARD.md) (the eleven Evidence pages and the deploy)
and [`FOR_REVIEWERS.md`](docs/FOR_REVIEWERS.md). Those files carry the
*explanation*; this one carries what it cost to learn, and the two should not
start duplicating each other. A change to how a layer works usually needs an edit
in `docs/` **and** here.

[`RUNNING_AS_A_SERVICE.md`](docs/RUNNING_AS_A_SERVICE.md) (2026-09-02) is the one
file in `docs/` that describes **something the repo has not built** — an
always-on deployment, why it is a `just serve` recipe rather than a container,
and publish-and-swap around the single-writer lock. It says so in its first
paragraph, which is the only thing stopping a reader typing a recipe that does
not exist: `tests/test_course.py` checks backticked paths and `just` recipes in
the *course* and the *skills*, never in `docs/`, so a doc proposing unbuilt
tooling fails no guard.

**Writing it corrected a skill, which is the part worth carrying.**
`querying-the-warehouse` said `just sql` "can sit alongside a build" because it
opens read-only. Measured on the pinned DuckDB 1.5.5, across processes and in
both directions, that is false: the rule is **one writer XOR many readers**, so a
read-only connection fails while a build holds the file and a build fails while
anyone is reading it. `read_only=True` buys compatibility with other *readers*,
never with a build — the one read that genuinely works mid-build is
`lake.lakehouse.read_only_connection()`, which opens the catalog and never the
warehouse. The measured table lives in that skill; the second finding, that
Evidence's `filename` can be redirected by `EVIDENCE_SOURCE__warehouse__filename`
but is `path.join`ed onto the source directory so an absolute path is silently
relocated, is in `building-evidence-reports`. Both are skill knowledge rather
than a new section here.

**[`PRACTICES.md`](docs/PRACTICES.md) is the odd one out and is the README's main
entry point** (2026-09-01): not a topic but an *index over* the topics — each
practice this repo demonstrates, the failure it prevents, the number that
measures it, and a link to where in the code it happens. It is deliberately thin
on argument, because the argument is here. **The risk it carries is the one the
docs split already names**: it restates figures that live in five other files, so
a claim added to it is a claim to keep in step. `tests/test_documented_counts.py`
covers the test, mart and additivity counts in it; nothing covers the rest.

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

Two of those moved on 2026-09-01 and the reasons are worth keeping.

- **`ingest/` is six source modules plus a coordination layer**, where it was one
  1,540-line namespace holding 8 resources, 45 module constants, 5 URL builders,
  3 watermark functions, the rate limiter and `main()`. Measured by *statements*
  no function was oversized (the largest is 24), which is why an earlier review
  filed the split as a decoy — it was measuring complexity, and the defect was
  cohesion. The dependency closure partitioned almost perfectly: 26 names
  exclusive to weather, 11 to the World Bank, 10 each to the ECB and retail, and
  **only five shared**.
  - **It made one real dependency visible.** `weather_locations` reads capital
    coordinates from the World Bank's country endpoint, so
    `ingest/sources/weather.py` now says `from ingest.sources import worldbank`
    where the flat file made it look like a local helper.
  - **The four coordination tuples deliberately did *not* become per-source
    metadata**, which is the half of the finding that was wrong.
    `PARTITIONED_RESOURCES` carries a comment arguing the rule across all four
    candidates *comparatively* — "`ecb_fx_rates` merges for the same reason WDI
    does and is deliberately not partitioned" cannot be read if the two facts
    live in different files. Deriving the tuples would have filed a comparison
    in six places.
  - **Shared helpers are reached as `http.get_json(...)`, never imported by
    name.** `tests/test_ingest.py` monkeypatches that function in ten places via
    a *string literal* (`setattr(http, "get_json", …)`), which no reference
    rewrite can see. Binding the name into each source would leave those patches
    pointing at something nothing looks up — green tests against the live fetch
    path. The same trap is why the split refused a re-export facade in
    `ingest/pipeline.py`: without one the stale patches raise `AttributeError`,
    which is how they were found.
- **`publish/` exists because `orchestration/assets.py` imported the top of its
  own dependency graph out of `scripts/`.** `export_warehouse.py` is 853 lines
  and *is* the publication boundary — the personal-data policy, the storage
  ceiling, attribution — in a directory whose name said "helper". The three
  load-bearing modules moved; the three genuinely one-off ones stayed.
  - **It also made `pages.yml`'s allowlist accurate.** `scripts/**` was a
    trigger path only because the directory was mixed; it is now on the
    not-an-input side, and `publish/**` is the trigger. The guard in
    `tests/test_workflows.py` caught the move and made the classification a
    decision rather than an oversight.

## The package (`src/modern_data_stack/`)

The domain-neutral mechanisms live here — `paths`, `fixtures`, `ducklake`,
`observability`, `export`, `history`, `db` — and take their configuration as
arguments. The project modules that call them (`ingest/fixtures.py`,
`lake/lakehouse.py`, `transform/pipeline_status.py`, `publish/export_warehouse.py`,
`publish/restore_history.py`) hold this project's constants and stay the entry
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
| `just dbt-build` | `dbt deps` then `dbt build` (32 models, 2 snapshots, 7 seeds + 460 data tests + 31 unit tests) |
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
| `just where` | print which warehouse file and landing zone the recipes will use — dbt's own log line names the *target*, never the file |
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
  contributor's commit hook rejects. **`ci.yml` runs `just lint` rather than
  `sqlfluff` directly**, so those paths are stated once instead of in the
  workflow and the recipe both.
- **The `just lint` hook means CI has to install `just`.** `ci.yml` runs
  `pre-commit run --all-files`, and a `local` hook whose entry is a recipe fails
  with "Executable `just` not found" on a runner that hasn't got it — which is how
  the hook shipped green locally and red on the first push (`uv tool install
  rust-just` + `$GITHUB_PATH` is the fix). Anything else moved into a `local` hook
  inherits the same requirement. **`.github/actions/setup` does that install for
  all four workflows now**, which is also what made routing the pipeline itself
  through recipes free — the requirement this bullet describes had already paid
  for the tool.

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
| `skill-creator@claude-plugins-official` | authoring and evaluating the project skills below — the one entry here that is about the repo's own tooling rather than a layer of the stack |

**Two rows left that table on 2026-09-02 and the measurement is again the whole
reason.** `dagster-expert@dagster` and `polars@polars` had **zero Skill
invocations across 211 transcripts** spanning 2026-08-09 to 2026-09-02 — and the
window is not the excuse, because 9 commits touched `orchestration/` and 9
touched `transform/` inside it. The work happened and neither was reached for.
Both are skill-only (one `SKILL.md` each, no LSP, MCP, command, hook or agent),
which is what makes a zero count admissible at all — the caveat that protects
`ty-lsp`, whose surface never appears as a `Skill` call.

- **`dagster-expert` is the clear one, and it is the `duckdb-skills` argument
  again.** Its own description sells the **`dg` CLI**, which this project
  deliberately does not install — `dg` is in neither `pyproject.toml` nor
  `uv.lock` — and `dagster-graph-and-jobs` covers Dagster *in this repo*, three
  jobs and two partitioned assets and all. A vendor skill about tooling the
  project has costed and refused is ~140 tokens of description arguing for a
  different project.
- **`polars` is the weaker call and is recorded as weak.** Nothing replaces it:
  there is no project skill for the two Polars transforms, so this is the first
  entry to reconsider if `transform/` ever grows into a layer. What decided it
  is that the two transforms *were* edited in the window, nine times, without it.
- **Both marketplaces stay registered**, for the reason `astral-sh` does:
  removing a `github` marketplace *uninstalls* its plugins and the project
  declaration does not silently bring it back. Re-enabling either is one line.
- **`false` is not how a plugin is retired here.** The working tree carried
  `dagster-expert@dagster: false` for a while and nothing noticed, because
  `tests/test_plugin_settings.py` read `list(enabledPlugins)` — the *keys* — so a
  disabled plugin was indistinguishable from an enabled one while this table
  still described both as live. The guard reads values now and a `false` entry is
  its own failure.

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
cache directory survives, so the only symptom was `Total LSP servers loaded: 1`
in the debug log and three skills quietly missing. The user-level entry for a
github marketplace is therefore not duplication of the project one — leave it.

**That symptom has since inverted, which is worth knowing before reading an old
debug log.** `Total LSP servers loaded: 1` used to mean the `astral-sh`
marketplace had gone missing; it is the *correct* state now that `astral` is
deliberately not enabled, and it is `2` that would mean something changed. The
line to grep is `already handled by`, not the count — no output is passing.

**`astral@astral-sh` and `duckdb-skills@duckdb-skills` were enabled and are
not any more, and the measurement is the whole reason.** Across 187 transcripts
(2026-07-29 to 2026-08-27) neither was invoked once: `duckdb-skills` cost ~670
tokens of always-loaded descriptions for nine skills about ad-hoc file querying,
S3 and spatial joins, none of which this project does — `querying-the-warehouse`
covers DuckDB *in this warehouse*, lock and all. `astral` is the more
interesting one, because it was doing worse than nothing.

- **It lost the `.py` claim by design, and that made it dead by
  construction.** `astral` and `ty-lsp` both declare a `ty` language server for
  `.py`/`.pyi`; the first loaded wins, so `ty-lsp` had to be declared above it,
  and the loser is two `[WARN]` lines in `~/.claude/debug/latest` that nothing
  surfaces. The ordering rule worked for as long as it was the invariant. What
  it also did was leave `astral` with no reachable surface at all: its LSP
  declares those two extensions and nothing else, and its three skills (ruff,
  ty, uv) were never once invoked. A plugin whose every surface is unreachable
  is two warnings.
- **Which server would have won is still why `ty-lsp` is the survivor.**
  Astral's runs `uvx ty@latest server`, the newest published ty on every launch,
  against a `just typecheck` that runs the version in `uv.lock`. ty is 0.0.x and
  its diagnostics move between patch releases, so letting theirs win means the
  editor showing findings the recipe cannot reproduce — the sqlfluff 3.3.0/4.2.2
  split in a new outfit.
- **The `astral-sh` marketplace stays registered, and `duckdb-skills`' does
  not.** Removing a `github` marketplace *uninstalls its plugins*, and the
  project declaration does **not** silently bring it back: a re-register needs a
  clone, which a non-interactive session will not do. Astral is one line from
  being re-enabled and is left that way; duckdb-skills is a decision, so its
  marketplace goes too.
- **`tests/test_plugin_settings.py` carries the invariant forward as an
  *absence*.** It used to assert `ty-lsp` sorts before `astral`; it now asserts
  `astral` is not enabled at all, with the ordering rule in the failure message
  for whoever re-adds it. JSON has nowhere to put a comment, which is why either
  version has to be a test. Check by hand with `claude --debug -p ok` then
  `grep 'already handled by' ~/.claude/debug/latest` — **no output is the
  passing state now.**

Astral's ty skill said to add an ignore comment only when the user asks for one,
and this repo carries two with the reason written next to them (see the ty
bullets under *Style guide*). That was a considered disagreement rather than
drift while the skill was loaded, and it is worth keeping written down: the
suppressions outlive the plugin that would have argued about them.

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

Eleven of the fifteen were split out of this file rather than written fresh:
domain or task reasoning that only one session in ten needs, against a file
loaded in full before every one. **A new section here is a question about where
it belongs, not only about what it says.**

Three splits so far, and the shape of them is the argument for the rule:

| Date | CLAUDE.md, at that commit | Skills after | What forced it |
|------|---------------------------|--------------|----------------|
| 2026-08-24 | 1,805 → 1,151 | 8 | the first attempt |
| 2026-08-27 | 2,309 → 1,460 | 11 | it grew back past its own starting point |
| 2026-09-01 | 1,961 → 1,441 | 15 | caught before the ceiling rather than at it |

**The first two rows are working-tree measurements and `git show` does not
reproduce them** — both splits shipped inside a commit that also added a feature
(the course, then weather), so the committed endpoints are 1,652 → 1,206 and
2,159 → 1,675. The third row is git-checkable because that split is its own
commit, which is the cheap fix and the reason to keep doing it that way.

**Growth is the number that matters, and the third split is the first one that
did not wait for it.** The file gained 953 lines in the two days after the first
split (476/day) and 286 in the four days after the second (72/day) — so it grew
back six times *slower* and was still split sooner, at 1,961 rather than 2,309.
It does not drift upward, it accretes in bursts behind feature work, which is
why the check belongs at the end of anything large rather than on a schedule.
`tests/test_course.py` globs `.claude/skills/*/SKILL.md` rather than listing
them, so every path and `just` recipe a skill cites is checked whether or not
anyone remembers the guard exists. **What it does not check is a markdown
anchor**: `docs/WAREHOUSE.md` linked at `CLAUDE.md#cbam-exposure-…` for three
days after that heading became a skill, green in review and dead on click. That
is now a test too.

**It does not scan this file, and the third split is what proved that matters.**
Moving prose into a skill subjects it to a check the original never had, and two
of the four blocks moved failed it immediately. Applying the same rule to
`CLAUDE.md` by hand: of 51 backticked paths it cited, **four pointed at nothing**
— and none of the four was rot, which is the reason extending the scan is not
free. Two named things that are *deliberately* gone or absent (the deleted hive
archive; the `explore.md` that must never exist under `reports/pages/`, because
Evidence reserves the route), and two were **Dagster asset keys that share a
path's shape** — `lake/parquet_archive` and `reports/evidence_site`. The second
pair is the blocker: `reports/evidence_site` is a current, correct citation of a
live asset, and the guard has no way to tell it from a dead file. Asset keys
elsewhere collide with nothing (`raw/`, `analytics/`, `marts/` are not directories
in this tree); it is only `lake/` and `reports/` that are both. The rewording is
done in the skills; scanning this file would need that ambiguity resolved first.

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
- `intermediate` — dbt views, `int_*`, and the layer with the fewest models on
  purpose: three, each earning its place by removing a specific cost rather than
  by completing a diagram. `int_country_year_observed` (the country-years the
  four country-stats sources report — derived twice, in `dim_country_year` and
  `fct_emissions_energy`, until it wasn't), `int_cbam_default_factors` (Annex I's
  fallback rule, separately true so separately testable) and
  `int_retail_return_matches` (the returns-to-purchase inference). `private` and
  uncontracted, like staging; they do not ship as Parquet
- `marts` — dbt tables, **one folder per mart**: `country_stats/` (6),
  `reference/` (6), `retail/` (5), `compliance/` (3). The models are:
  `dim_country` (**the conformed country dimension** — one row per
  `country_iso3`, 228 of them, and what every other model's country key joins
  to), `dim_country_year` (the country-year spine, that crossed with the years),
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
  `fct_fx_rates_periods` (month / quarter / half / year) — `dim_country` sits in
  the same group and is the exception to "no country in them". Plus the five retail
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

**"Mart" means the subject area, not the file, and this repo used the word both
ways until 2026-09-01.** In the BI sense a mart is the view a department works
with — so there are **four** here, and they are `dbt/models/_groups.yml`:
`country_stats`, `reference`, `retail`, `compliance`. `marts/` is dbt's name for
the *layer*, and the 20 relations inside it (19 models, one of them versioned)
are **mart models**. The docs counted models and called them marts, which is how
a stale count of 17 survived two additions to the layer;
`tests/test_documented_counts.py` guards the number now and the folders make the
four visible in the tree. (Quoting the old claim in its original wording here
failed that guard, which cannot tell a quotation from an assertion and should
not try — the same trap as the pytest-count phrasing under *Testing*.)

- **The folders are one per group, and `+group:` is set on the folder** in
  `dbt_project.yml` rather than on each model — it was restated 18 times in files
  whose own names said it. `+schema: marts` is inherited by all four, so the
  relation names, the release layout and the Dagster asset keys are untouched by
  the nesting.
- **Consolidating the models was considered and measured against.** Three pairs
  share a grain within a group, and every one is sparse against its partner:
  `fct_retail_returns` is 18,286 rows against `fct_retail_order_line`'s
  1,067,371 (1.7%), and `fct_country_weather_year` covers 41 countries against
  `fct_emissions_energy`'s 228. Merging either would mean columns null on 98% of
  the rows. One fact table per business *process*, not per grain.
- **Normalising the country attributes out of the facts was considered and
  measured against, and only half of it was done.** `dim_country` shipped
  because five models across three groups were reading a *staging* model for
  want of a published dimension and the release made a consumer deduplicate a
  62,928-row spine to find 228 countries. Stripping `country_name`, `region` and
  `income_group` back out of the five facts that carry them did **not**, and the
  numbers are why: it saves **6.7 kB of a 1,591 kB Parquet — 0.4%** on
  `fct_emissions_energy`, because zstd dictionary-encodes 228 repeated strings
  to nearly nothing, and **no copy can drift** — every one is built from the
  dimension in the same run, and all 71,000-odd rows across the six relations
  agree with it today, measured. The cost is 8 Evidence pages, 2 source queries
  and `transform/co2_intensity.py` (which ranks *within* `income_group`) each
  gaining a join, plus a v3 of the one versioned model. Kimball's rule against
  dimension attributes in a fact is a row-store storage argument; in a columnar
  file it buys 0.4%.
- **The near-miss is `fct_fx_rates_published`**, which `fct_fx_rates_daily` is a
  strict superset of (`where is_published_rate` recovers it). It stays a mart
  model because it is the project's only incremental model and the site reads it
  directly, but it is the one relation here that is arguably an intermediate
  concern in the presentation layer.

Grain of every *country* fact/staging model is **`(country_iso3, year)`**; joins
are on ISO3 country code + year. The country dimension (`marts.dim_country`,
one row per country) supplies `region` and `income_group`. Two of those models are Eurostat prices at their
published `(country_iso3, year, half)` grain —
`stg_eu_electricity_prices_semiannual` and the mart off it — and they are the
exception on purpose, not a model waiting to be flattened; the `country-stats-models`
skill has the half-over-half movement that makes the annual average a price nobody
paid.

**That sentence used to say "every model", and it stopped being true twice.**
`fct_cbam_exposure` has no year (a regulatory schedule, not a time series) and
the FX tables have no country. The country-year spine is the *dominant* grain
here, not a house rule — reaching for `dim_country_year` when the thing being
modelled isn't a country-year is how you get a fact with a fabricated dimension
on it.

**The fact hangs off the spine, not off a source.** `dim_country_year` is
`dim_country` × every year the data covers (bounds read from the sources, so both
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
- **The published history is carried, not rebuilt** (`publish/restore_history.py`,
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

Five domains carry enough hard-won detail to be worth loading on demand rather
than in every session. The models are listed under *Warehouse schemas* above;
the reasoning lives in `.claude/skills/`. **The table is one row per dbt group
plus the two sources that cut across them** — which is what makes a missing row
visible: `country_stats` was the last group with no skill, and its knowledge sat
loose in *Conventions & gotchas* until 2026-09-01 for exactly that reason.

| Domain | Skill | What is in it |
|--------|-------|---------------|
| OWID, the World Bank and Eurostat (the `country_stats` group) | `country-stats-models` | coverage that thins per column, current against constant dollars, territorial against consumption emissions, the WDI window and its restatements, and Eurostat's semi-annual grain |
| Scope 2 factors and CBAM (the dbt `compliance` group) | `compliance-models` | the vintage filter that cannot be a year literal, the fabricated worked example, the annex transcription policy, the 2026/1740 migration, and why Annexes II–IV are left out |
| Retail transactions (the `retail` group) | `retail-models` | the three cleaning decisions whose wrong answers are plausible, the returns inference, the ragged cohort triangle, why `ntile(5)` is wrong for RFM, and the country map that joins retail to the country domain |
| ECB rates and the calendar | `currency-and-calendar` | the 7-day carry-forward cap, spot against average, ISO year against calendar year, and the project's one incremental model |
| Capital-city weather (`om_weather_daily`) | `weather-models` | the weighted rate budget that bounds the whole source, the positional multi-location response, the three-year cold start, and the two degree-day conventions |

The one-liners that must not depend on a skill loading are already in *Warehouse
schemas* above and stay there: `fct_example_scope2_emissions` is the only
fabricated data in the warehouse and it ships in the public release,
`fct_cbam_exposure` has no year in its grain, and the retail models are the only
ones at a grain below a country. Weather's is one line up, in the `raw` bullet:
`om_weather_daily` is the second table a rebuild cannot reproduce, and the three
places that guard the first one have to name it too. Country stats has four, in
*Conventions & gotchas*, because they change what a query *means* rather than
merely how to write it.

## Personal data (`meta: {pii: …}`, `publish/export_warehouse.py`)

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
- **There are thirty-one unit tests, over twelve models, and they exist because a data
  test cannot see a wrong answer that is a legal one.** `dim_date`'s
  `fiscal_quarter` carries `accepted_range 1-4`, which is what caught the
  `/3 + 1` float-division bug at quarter *5*. Change the same expression to `/ 4`
  and every fiscal quarter in the warehouse is wrong while **all 19 data tests on
  the model pass** — measured, not argued. Its three unit tests fail on it.
  **Which twelve models, what mutating each one proved, and the fixture shapes
  strong enough to catch it are the `unit-testing-dbt-models` skill**, together
  with the mutation method that produced all of it.
- **A unit test that has to mock five inputs is telling you a model is two
  models.** `cbam_exposure_takes_the_fallback_row_whole_or_not_at_all` checked
  one rule — the annex fallback is row-level, not column-level — and posed a
  markup schedule, a country dimension and an empty `dim_grid_emission_factors`
  to reach it, then asserted on a `certificates_*` column computed downstream of
  the rule. Two of those inputs were inert; one carried a comment explaining why
  an unrelated table was in the fixture at all. Against
  `int_cbam_default_factors` it mocks one input and asserts four columns. **The
  fixture size was the signal, and it was visible for months** — `_unit_tests.yml`
  averaged 45 lines a test. That is the argument for the intermediate layer here,
  and it is why there are three `int_*` models and not one per mart: the other
  two removed a duplication and named an inference, and nothing else qualified.
- **A determinism guard has to be mutated *repeatedly*, and this one was flaky
  rather than blind.** `return_matches_break_a_tied_purchase_the_same_way_every_build`
  pins the `qualify` tie-break that made `int_retail_return_matches` reproducible,
  and it passed with that `qualify` deleted — at HEAD and at the commit before
  the test moved, so it was its own blind spot. The first diagnosis was that
  dbt's mocked input arrives in an order that lands the un-tie-broken `asof
  join` on the same row anyway; **that is wrong**. DuckDB's *parallel* asof join
  draws a different tied row each run — 300 runs of the compiled SQL returned
  all three, 135/86/79 — so the old test passed a broken model **28.7% of the
  time**, and the two spot-checks that called it broken-but-stable were unlucky
  draws. `threads=1` is deterministic and takes the first-listed row.
  - **No fixture makes it certain**: the mutated model returns *some* member of
    the tie group and the tie-break's answer is always a member of it, and the
    row count is 1 either way. Four independent tie groups take the false pass
    to **1.1%** (22/2,000) and to 0% single-threaded; the healthy model is stable
    at 1, 2, 4 and 8 threads, so nothing is flaky in CI. Extra returns into one
    group buy nothing — the arbitrary pick is made once per group (300/300
    identical), so it is *groups* that multiply.
  - The winner is never listed first (first is what the broken join takes) and
    sits at a different non-first position in each group. Group 1 is unchanged:
    reordering it would pin today's arbitrary pick, which is the bug wearing the
    fix's clothes. `dim_retail_customer` had already learned this and the lesson
    had not been carried across — see the `unit-testing-dbt-models` skill.
- **Unit tests run inside `dbt build`, and they are deliberately left there.**
  dbt Labs recommends excluding them from production runs to save compute; that
  argument is about warehouse spend and this is a local DuckDB build where all
  thirty-one cost 4.4s. A broken fiscal calendar should stop `release-data.yml`,
  not ride along in it. `just dbt-unit-test` is the inner loop — 4.3s of dbt's
  own time, ~10s wall once `dbt deps` and startup are counted.
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
  only two places one domain reads another's cleaning layer, and
  `dim_grid_emission_factors` deliberately re-models `stg_energy`'s intensity
  column for a different reader and needs `source_loaded_at` with it. Both
  reasons are written next to the override. Enforcement is real and was verified
  by breaking it: flipping `stg_country` to `private` fails `dbt parse` naming
  its consumer, not `dbt build` an hour later.
  - **`stg_country`'s override got smaller when `dim_country` shipped, and the
    difference is the point.** It used to be read by marts in two other groups
    for want of a published country dimension; they read `marts.dim_country`
    now. What is left is two *staging* peers in `country_stats` —
    `stg_weather_daily` needs the capital coordinates and
    `stg_eu_electricity_prices_semiannual` the ISO2→ISO3 map — so breaking the
    override names `stg_weather_daily` today where it named `fct_cbam_exposure`
    before. Pointing those two at a mart would invert the layering to save an
    override, which is the worse trade.
- **Marts are `public` because the release makes them so.** Every mart ships as a
  standalone Parquet file to people who cannot be paged; `access` is a statement
  about that, not about the repo.
- **Contracts are enforced on every mart model — 20 relations (19 models, one of
  them versioned) and 397 columns, each with a `data_type`.** The ymls documented 179 of those columns before, so the list was
  *generated* from the built warehouse's `information_schema` and inserted
  line-wise, reordering the existing entries into SQL order and keeping every
  description untouched. A PyYAML round-trip would have reflowed 1,246 lines of
  prose to add scalars; **don't do that to these files**, and that constraint is
  what shaped the split below as well.
- **The marts declarations are one yml per dbt group, not one per layer.**
  `_country_stats.yml` (5 models, 680 lines), `_reference.yml` (5, 536),
  `_retail.yml` (5, 526) and `_compliance.yml` (3, 450) replaced a single
  2,183-line `_marts.yml` on 2026-09-01. dbt does not care which file declares a
  model, so the boundary had to come from somewhere — and `_groups.yml` already
  declares exactly four domains with enforced `access` between them, which makes
  the split the one dbt itself can check. A layer-shaped split would have put
  every mart model in one file and changed nothing.
  - **The move was line-slicing, and the guard was a manifest diff.** Blocks
    were relocated as bytes and asserted byte-identical afterwards; then a
    fingerprint of every marts node in `manifest.json` — group, access, alias,
    version, contract, materialization, description, and each column's
    `data_type` and description, plus every test node hanging off a marts
    model — was compared before and after and came back **identical**. `dbt build` stayed at PASS=508 ERROR=0.
    A green build proves the yml parses; only the diff proves nothing moved.
  - **The reason it was worth doing is the merge lock, not the line count.**
    Shared prose "behaves like a lock" (see *Branches and PRs*) — no two commits
    touching it can be reordered or cherry-picked — and a 2,183-line file every
    mart change edits is that problem for the model layer.
  - **`_unit_tests.yml` deliberately stayed whole.** It is one axis of assertion
    across twelve models; the four group files are four domains. Splitting it
    too would have cut the same tree twice on different lines.
  - **A file naming one of these is a list that can go quiet**, which the split
    proved by breaking one: `tests/test_privacy.py` named `_marts.yml` and would
    have read a quarter of the marts layer. It globs now and derives the
    expected set from the `.sql` files, so a fifth group file — or a model whose
    block goes missing in a move — is a failure rather than a silence.
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
    it rather than assumed — every contract holds on the slice.
- **Exposures are per *page*, not per site, and they are checked.**
  `dbt/models/_exposures.yml` declares nine Evidence pages and the monthly data
  release, so `dbt ls --select +exposure:evidence_retail` answers "what breaks if
  I change this" for one page. `publish/build_report.py` gained `page_tables()`
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
  - **The release exposure is exactly the marts now, and was not.** It named
    `stg_country` — a *staging* model in the promise a release makes — because
    the notes pointed a reader at it as the country dimension and no mart said
    the same thing. `dim_country` closed that, so `tests/test_exposures.py`
    asserts the exception's *absence* rather than its shape. The nine staging
    views still ship as Parquet and nothing promises them.
- **Every numeric mart column declares `meta: {additivity: …}`**, from a closed
  four-value vocabulary — `additive`, `semi_additive`, `non_additive`,
  `not_a_measure` — because a contract states a type and a test states
  correctness, and neither says whether `sum()` means anything. 117 of the 226
  are non-additive. **Counted as dbt resolves them, which is the basis every
  figure in this section uses** — `fct_emissions_energy_v1` inherits 36 labels
  through `include: all` and declares one, so the ymls carry 190 literal
  `additivity:` entries where the manifest carries 226 labelled columns (189 +
  v1's 37). Quoting the yml count while naming the manifest one is how a stale
  pair survived into a release — described in words rather than digits here,
  because the guard cannot tell a quotation from an assertion and should not
  try. `tests/test_additivity.py` holds five properties, each mutation-proven:
  the layer is covered exhaustively, the vocabulary is closed, only numeric
  columns are labelled (the vacuity guard), and **no column named
  like a ratio may be declared summable** — a name rule, because it is the only
  one of the four that can catch a label that is present and *wrong*. It holds
  today with no exceptions.
  - **`semi_additive` is the only label that is useless alone**, so its
    description must say which direction fails and a test requires one. There
    are 16 `semi_additive` columns, three of them `fct_emissions_energy_v1`'s
    inherited copies, and they are where the value is: `population` adds across
    countries
    and gives person-years across years, `cumulative_co2` is a stock that
    recounts every earlier year, `cohort_size` is constant down a cohort's rows,
    and `original_quantity` belongs to the matched purchase — 16,398 matched
    returns point at 15,312 distinct purchases, so summing it counts 1,086 of
    them twice.
  - **`gdp_usd` is `semi_additive` and `gdp_constant_usd` is `additive`**, which
    is the current-vs-constant-dollar gotcha under *Conventions & gotchas*
    expressed as metadata rather than as prose somebody has to have read.
  - **The labels ship**, in `manifest.json`'s `additivity` map — 282 columns
    across 25 relations — for the reason `direct_identifier` is real: a label
    with no consequence is decoration, and a Parquet consumer has the types and
    nothing else. The five `analytics` tables are invisible to dbt, so their 56
    are `EXTRA_ADDITIVITY`, beside `EXTRA_CLASSIFICATIONS` and for its reason.
    `staging` stays outside: its measures are declared one layer up.
  - **The `analytics` copies are stated, not inherited, and that is the whole
    of the choice.** `co2_intensity` is `select *` off `fct_emissions_energy`
    plus two derived columns, so 37 of its labels are the mart's — deriving
    them at runtime is less typing and fails *open*, because a mart rename would
    take the copy's label with it silently. Stated, the same rename fails
    `test_a_copied_column_keeps_the_label_the_mart_gave_it`, which checks the
    column *set* as well as the values and was mutation-proven from both sides.
    `retail_rfm` cannot be reached by name at all — `frequency` is
    `n_orders` and `monetary_gbp` is `net_revenue_gbp` — so its coverage is
    asserted against the frame `build_retail_rfm` actually emits.
  - **Inserting the labels line-wise found the duplicate-key trap twice.** A
    `meta:` block can sit *below* a comment block or a `description:`, so a
    lookahead that only skipped comments wrote a second `meta:` key — which
    PyYAML resolves silently by taking the last, dropping a `pii`
    classification, and which `check-yaml` does not flag. The insertion has to
    scan the whole column block. Same trap as the `unit_tests:` one above, and
    just as quiet.

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

- **The bus matrix is derived from the manifest, never written**
  (`modern_data_stack.bus_matrix`, `publish/bus_matrix.py`, `just bus-matrix`),
  and it renders into a marked block in `docs/WAREHOUSE.md`. Business processes
  down, conformed dimensions across — the one thing groups, exposures and
  contracts do not say, which is why retail sat beside the country domain for
  months with no joinable key. Three things it cost to get right:
  - **A uniqueness test carrying a `where` is not a grain.**
    `dim_grid_emission_factors` asserts one row per `country_iso3` *where
    `is_latest_available`*. Read as a grain it becomes a conformed country
    dimension and every country fact appears to conform to it — a column of
    marks that mean nothing. `declared_grains` skips filtered tests.
  - **Conformance is exact column-name matching, deliberately.** An alias list
    would hide the defect the matrix exists to expose: `fct_fx_rates_periods`
    and `fct_fx_rates_published` carry `quote_currency` where `dim_currency`
    publishes `currency_code`, and `fct_fx_rates_daily` spells it the conformed
    way. Rendering that as a tidy row would be the matrix arguing against
    itself. A hole is a question, not a bug in the derivation.
  - **The rendered block is guarded, not just generated.**
    `tests/test_bus_matrix.py` regenerates and compares, so a mart added without
    `just bus-matrix` fails rather than leaving a confidently wrong table. Its
    orphan set is compared *both ways* against `KNOWN_UNCONFORMED`, so fixing an
    orphan without deleting its entry fails too. Being manifest-gated, it had to
    be added to `ci.yml`'s post-parse step — `tests/test_workflows.py` caught
    that omission, which is the guard working rather than a near miss.

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
  is what dbt does; 438 of the 460 tests use the default. `severity` comes across
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
  (36 tables today, not 39).
- **It must run after `dbt build`** — it reads `dbt_test__audit` and the
  manifest, neither of which exists before one.

## The lakehouse (`lake/lakehouse.py`)

**dlt lands `raw` in a DuckLake catalog under `data/lakehouse/`, and the DuckDB
file holds only what dbt builds.** dbt attaches the catalog (`profiles.yml`'s
`attach:`, `_sources.yml`'s `database: lakehouse`) and writes `staging`, `marts`,
`history` into `data/warehouse.duckdb`, which is the whole of what the release
publishes. `just lakehouse` *reports* the catalog; `just ingest` is what fills it.
It replaced a hand-written hive archive (`lake/archive.py`, `data/lake/`), which
was a second copy of the warehouse maintained by hand.

**The mechanics are the `the-lakehouse` skill** — why dlt's merge makes
DuckLake's change feed useless and what replaces it, reading table versions out
of the catalog database, the allowlist that decides what
`lakehouse.tar.gz` publishes, the unpinnable extension and the spec-version
guard, and how to migrate a tree that predates the move. Four things stay here
because they bite outside that task:

- **`just sql` attaches the lakehouse, and without it a third of the warehouse
  does not open.** The nine `staging` models are *views* over `lakehouse.raw`, so
  a bare `duckdb data/warehouse.duckdb` binds the 26 `marts`/`analytics`/`history`
  relations and fails every one of the 9 staging views with `Catalog "lakehouse"
  does not exist!`. The recipe attaches in the same mode as the warehouse, so
  `just sql write` can repair a landing table and the default cannot touch one by
  accident.
- **It is the only copy of every landing table, so `just clean` still does not
  take it.** Deleting it costs the snapshot lineage *and* the weather archive,
  which is days of Open-Meteo budget. Both silently.
- **`just` exports an absolute `LAKEHOUSE_DIR` for every recipe**, which is not a
  convenience. DuckLake compares the stored `data_path` against the given one **as
  strings**, so the same directory under two spellings is refused —
  `profiles.yml`'s relative default made dlt (from the repo root) and dbt (from
  `dbt/`) disagree, and the error lands in `dbt build`, one layer downstream of
  the layer that chose the spelling. **No recipe could reproduce it, because every
  recipe exported the variable that hid it.** `WAREHOUSE_PATH` gets away with a
  relative default because a plain file keeps no such record.
- **`.github/actions/setup` is the one definition of that environment** — uv, the
  venv, `just`, and all three paths absolute — because until 2026-09-01 the four
  workflows each set the paths themselves, so all four needed the same new line
  when the landing zone moved and none of them got it. Three tests in
  `tests/test_workflows.py` hold it: the action must export all three (the vacuity
  guard — the other two assert an *absence* and would both pass if nothing set
  them at all), no workflow may define one itself, and every workflow running the
  pipeline must use the action.
## Publishing (`publish/export_warehouse.py`)

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
- **The rest of the boundary is the `publishing-a-release` skill** — the two
  format ceilings and the moments their tripwires fire, why `data_loaded_at` has
  to name the catalog rather than the copy, and the rules that carry the
  unreproducible tables forward. Three of its results are load-bearing outside
  that task:
  - **The manifest carries both `duckdb_version` and `storage_version`**, because
    "who wrote this" and "can I open it" are different questions with different
    answers: DuckDB 1.x writes format **64** by default, which every client back
    to v0.10.0 reads. `MAX_PUBLISHED_STORAGE_VERSION` is the ceiling and
    `test_the_installed_duckdb_still_writes_the_format_the_release_promises`
    checks the *toolchain*, so it fires on the Dependabot PR that moves DuckDB
    rather than on the artifact. **Not an upper bound on `duckdb`, deliberately**
    — `dagster<3.15` remains the only hard upper bound in the tree.
  - **`lakehouse.tar.gz` has the same shape of ceiling** (`ducklake_metadata.version`,
    `MAX_PUBLISHED_LAKE_VERSION`) and its tripwire has to work harder, because
    the DuckLake spec moves when extensions.duckdb.org republishes and **there is
    no PR to fail** — only the next CI run can say so.
  - **Each release carries the previous one's unreproducible tables forward**
    (`publish/restore_history.py`, `just restore-history`), which is what makes
    the published snapshot accumulate a real revision log instead of holding one
    version per row forever, and what keeps the weather archive deepening instead
    of resetting to a three-year cold start every month. `CARRIED` is a tuple of
    `Carry` rules and `irreplaceable_rows()` is the one count all three callers
    use — `just clean warehouse`'s gate, the restore step and the "did not
    shrink" verify — so a rule added there reaches all three at once. **Only "no
    previous release" may skip**; a failed download or restore is fatal in
    `release-data.yml`, because continuing would publish an empty history that
    the *next* release then inherits.

## Conventions & gotchas (learned the hard way)

- **Clean schema names** come from `dbt/macros/generate_schema_name.sql`, which
  overrides dbt's default `<target>_<custom>` (which would give `main_marts`).
  Reference marts as `marts.fct_emissions_energy`, not `main_marts.…`.
- **One dbt target, and that is a decision rather than an omission.** `dev` is
  the only output in `dbt/profiles.yml`. A target separates *schemas inside one
  database*, which is the whole of what `dev`/`ci`/`prod` do on Snowflake or
  BigQuery; DuckDB is a file, so `WAREHOUSE_PATH` swaps the entire database —
  a stronger separation — and the macro above deliberately keeps the schema
  names identical in every context, which is what lets
  `marts.fct_emissions_energy` resolve the same on a laptop, in CI and in the
  published release. Three outputs would differ in name only, selected by a
  second environment variable on top of the one that already decides
  everything. Measured before deciding: `target.` appears **once** in the whole
  project (`target.schema`, in that macro) and no `--target` is ever passed.
  The reasoning sits in `profiles.yml` beside the output it explains; a port to
  a real warehouse should add the targets, and `docs/REUSING_THIS_STACK.md`
  says so.
  - **What it costs is that dbt's one "where am I" line is uninformative.**
    `Concurrency: 4 threads (target='dev')` names the target and never the
    file, so `just dbt-build` against the real warehouse and against a course
    sandbox print the same line — which is the trap the course notes and the
    fixture-run warning both describe, from two directions. `just where` prints
    the file, and the eleven recipes that write to the warehouse or the landing
    zone take it as their first dependency, so a run announces its destination
    before reaching it. `just` runs a shared dependency once, so `just run`
    says it once. The three recipes that export `WAREHOUSE_PATH` themselves are
    excluded on purpose: they announce their own, and `where` would print the
    outer value.
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
- **dlt state is keyed on the pipeline *name*, not the destination.** So a
  fixture run would otherwise hand its WDI watermark to the next real run, which
  would fetch a five-year window into a warehouse that has no history —
  `build_pipeline()` appends `_fixtures` to the name under `INGEST_FIXTURES=1`
  for exactly that reason. (dlt does reset state when the destination is empty,
  which is why this only bites when the real warehouse already exists.)
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
- **The country-year data semantics are the `country-stats-models` skill**, and
  they are the largest body of "plausible number, wrong basis" in the repo:
  coverage that thins per column, OWID's territorial-vs-consumption pair, the
  World Bank's padded region names and missing ISO3s, Eurostat's JSON-stat grid
  and its two ISO2 exceptions, and the WDI incremental window. Four of them
  cannot wait for a skill to load, because they change what a query *means*:
  - **Divide by `gdp_constant_usd`, never `gdp_usd`.** `gdp_usd` is *current* US$,
    so it moves with inflation and the exchange rate. Of the 193 countries with
    both series in 2010 and 2024, **30 flip the sign of their decarbonisation
    trend** on that choice alone.
  - **"Latest year" is per column, not per table.** `max(year)` on the mart is
    whichever source runs furthest ahead, and coverage thins unevenly before it —
    `co2_mt` holds 214 countries where `primary_energy_twh` collapses to **79**.
    Read `reports/sources/warehouse/latest_years.sql`, never a literal.
  - **Eurostat prices are semi-annual.** Chart prices over time off
    `marts.fct_eu_electricity_prices_semiannual`; the annual column exists to
    join prices to emissions or GDP and is a price nobody paid.
  - **Adding a WDI indicator is two places** — `WB_WDI_INDICATORS` in
    `ingest/sources/worldbank.py` and a `max(case …)` in `stg_wdi.sql`, held
    together by `tests/test_ingest.py`.

- **Retail carries `country_iso3` now, and the join that resolves it has to stay
  a *left* join.** The source names countries in its own words, so the retail
  models could not be joined to the country domain at all;
  `retail_country_map` (a seed, 43 rows) resolves every label once in
  `stg_retail_lines`, and the fact, the returns fact, `dim_retail_customer` and
  `analytics.retail_rfm` all carry the key. 34 of the 43 labels match
  `dim_country_year.country_name` exactly, which is what makes a join on name
  look like it works while losing the other nine — `EIRE` alone is GBP 615,520
  and the second-largest market. The seed is exhaustive on purpose and
  `relationships(country → seed)` is what makes a label from a re-ingest loud.
  **An inner join does not trip that test, it defeats it**: it deletes the
  unresolved rows, so the test reads a model with nothing left to fail on —
  measured at 17,866 lines and GBP 615,520 gone, four customers with them, and
  all 90 nodes green. The nine labels, the six judgements and the `max_by` in
  the customer dimension are the `retail-models` skill.

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
- **Two assets are partitioned and three jobs exist because of it.**
  `raw/wb_wdi` is yearly and `raw/retail_invoice_lines` monthly;
  `define_asset_job` resolves a selection to a *single* `partitions_def` or
  raises, and there is no opt-out. So `load_retail` carries the retail ingest
  alone, `full_refresh` is `AssetSelection.all() - site - retail_ingest`, and
  **`load_retail` has to run first** because dbt reads the table it lands. The
  justfile recipes and all four workflows pair them; running `full_refresh` by
  itself against a fresh warehouse fails inside dbt with `Catalog Error: Table
  with name retail_invoice_lines does not exist!`.
- **Every asset and check is listed by hand in `definitions.py`, and nothing
  tells you when one isn't** — an omission is not an error, it is simply an asset
  the graph never sees, and `dagster definitions validate` passes.
  `tests/test_definitions.py` compares what `assets.py` defines against what the
  graph resolves, and CI runs it in the `dbt parse` step.
- **The rest of the graph is the `dagster-graph-and-jobs` skill** — what earns a
  partition and what a partitioned asset needs in order to keep working
  unpartitioned, the two traps in the registration test, and the costed decision
  not to be a `dg`-shaped project or to use declarative automation. The three
  facts about how the *site* meets the graph — the per-table deps map, the
  size-checking render guard, and Evidence's reserved route names — moved to
  `building-evidence-reports`.
- **The Evidence site is an asset, and it's the asset excluded from
  `full_refresh` for a reason that isn't partitioning.** `reports/evidence_site`
  shells out to npm via `publish/build_report.py`; `ci.yml`, `nightly.yml` and
  `release-data.yml` all run `full_refresh` on a bare uv checkout with no Node, so
  a site in that job would break three workflows to serve one. `pages.yml` runs
  `publish_site`. Both selections in `definitions.py` name what they leave out; a
  second npm-shaped *or* differently-partitioned asset would have to be excluded
  by hand too.
- **Importing `orchestration.assets` leaves a dlt pipeline active process-wide.**
  The `@dlt_assets` decorators call `build_pipeline()` at import time and dlt
  records the result as the ambient pipeline, so a later test calling a resource
  generator directly reads the real `~/.dlt` state instead of none —
  `test_wb_wdi_follows_pagination` starts asking for `&date=2021:2026` and fails
  on pagination it never got wrong. It only bites when the whole suite runs, and
  only on a machine that has loaded WDI at least once. `tests/test_definitions.py`
  deactivates the pipeline on teardown; anything else under `tests/` that imports
  the orchestration layer has to do the same.
- The `daily_refresh` schedule ships `STOPPED` on purpose — opening the UI
  shouldn't start hammering public APIs on a timer. It targets `full_refresh`, so
  it doesn't try to build the site either.
- Dagster state lives in `.dagster/` (`DAGSTER_HOME`, exported by the justfile).
  Only `dagster.yaml` is checked in.

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
- `just coverage` — line and branch coverage of that tier, ~18s, at 67% branch /
  78% statement today. Reports and gates nothing (no `fail_under`, not in CI,
  no plugin loaded into `addopts`) for ty's reason. Read it with the two caveats
  in `[tool.coverage.report]`: it measures the mocked tier only, so the
  transform and lake layers read low while `just test-pipeline` exercises them
  end to end, and some of what is uncovered is uncovered deliberately.
  - **`[tool.coverage.run] source` is a hand-maintained list of directories, and
    the 2026-09-01 split left it behind.** `publish/` was carved out of
    `scripts/` and the source list kept naming only `scripts` — so the
    publication boundary (the personal-data policy, the storage ceiling,
    `build_report`, `restore_history`: 342 statements) was measured by nothing,
    while the directory this file calls "genuinely one-off" still was. The same
    move updated `pages.yml`'s allowlist, which `tests/test_workflows.py` guards;
    nothing guards this list, so it went quiet instead. **Adding it moved the
    numbers *up*** — 77.6% → 78.5% statement, 62.9% → 67.1% branch — because
    `export_warehouse.py` is at 83%: the blind spot was hiding well-covered code,
    which is why no one noticed a number that looked plausible.
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
    table that no longer lives there — the `the-lakehouse` skill has the
    migration.
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
