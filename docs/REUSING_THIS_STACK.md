# Reusing this stack

How to start a *new* project on this shape (dlt → DuckDB → dbt → Polars →
Evidence, orchestrated by Dagster), using this repo as the reference
implementation.

**If you would rather start from a tree than a document**, there is one:
[`Ddscully/dlt-dbt-duckdb-template`](https://github.com/Ddscully/dlt-dbt-duckdb-template)
is this repo with §1's deletes done, §5's rename reduced to one script, and one
trivial source (monthly gold prices) left in, so `just test-pipeline` and the
asset graph are green from the first commit. It is a fork rather than a
generated cut, so the two trees drift by hand — and it carries the tree, not
the reasoning. Read this document either way.

This is not a checklist for adding a source to *this* warehouse; that's
[`.agents/skills/adding-a-data-source`](../.agents/skills/adding-a-data-source/SKILL.md).
It's the layer above: what carries over to a different dataset, what has to be
rewritten, and the handful of decisions that are expensive to change later.

**Most of what makes this repo work is not transferable code.** The pipeline is
~4,000 lines and the part with nothing domain-specific in it is maybe a third:
the layout, the wiring conventions, the CI shape, the lint config. The rest is a
worked example. The fastest way to reuse it is to copy the tree, keep the
skeleton and delete the emissions.

That third is already separated out, so you don't have to go looking for it:
it's `src/modern_data_stack/`, it takes its configuration as arguments, and the
project modules that call it hold the constants.

**This document was executed on 2026-09-15**, against `c054e53`: a clone
followed it literally, with one unrelated source (monthly gold prices, a month
grain and no country), until CI's `build` job passed. The "a third" held up for
the code. The lists below did not, and they are corrected from that run: the
package rename touched 63 files rather than six, `orchestration/assets.py` was
56% example, and three defects passed every local check and would have failed
only in CI or at the first release (the `*.csv` fixture in §4, since fixed
here, the export's personal-data refusal in §7, the lakehouse release check in
§7).

## 1. What you're actually reusing

### The package — `src/modern_data_stack/`

Eleven modules, no mention of emissions in any of them. Copy the directory, or
depend on it and write only the layers below.

| Module | What it does | Configured by |
|--------|--------------|---------------|
| `paths` | project root, warehouse file, lakehouse dir, dbt manifest | `PROJECT_ROOT`, `WAREHOUSE_PATH`, `LAKEHOUSE_DIR` |
| `fixtures` | serve recorded payloads instead of live endpoints | a list of `(url pattern, filename)` routes |
| `ducklake` | attach, publish and relocate a DuckLake catalog | an alias, a data path and a spec ceiling |
| `observability` | dlt/dbt/DuckDB metadata as queryable tables | landing-table and layer names |
| `export` | package a warehouse as a publishable artifact | schemas, attribution, a notes renderer |
| `history` | carry unreproducible relations forward between builds | a tuple of `Carry` rules |
| `privacy` | pseudonymise an identifier at the publication boundary | the classified columns and a salt |
| `ratelimit` | a sliding-window budget for an API that charges by volume | `(seconds, units)` limits |
| `workbook` | read a spreadsheet source without loading it whole | a URL and a batch size |
| `db` | single-row and scalar reads, without the `Optional` | nothing |
| `bus_matrix` | derive the bus matrix from the manifest's uniqueness tests | a schema and the `dim_`/`fct_` prefixes |

Each project module keeps the entry point, so `python -m lake.lakehouse`, the
justfile recipes and the asset graph all still call the same names. Four rules
keep the split a split:

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

### Config-only — copy the file, change the constants at the top

- `ingest/fixtures.py` — the `_ROUTES` table.
- `lake/lakehouse.py` — where the DuckLake landing zone lives, `PUBLISHED_TABLES`
  and the merge keys.
- `transform/pipeline_status.py` — `SOURCE_TABLES` and `LAYERS`.
- `publish/restore_history.py` — the schema name and the CLI.

### Copy verbatim — the tooling

- `.sqlfluff` — retarget `dialect` if you're not on DuckDB; everything else holds.
- `.pre-commit-config.yaml` — including the reasons the sqlfluff hook is `local`.
- `pyproject.toml`'s `[tool.ruff]`, `[tool.ruff.lint]` and
  `[tool.pytest.ini_options]` blocks. The `extend-select` list and
  `combine-as-imports` are both load-bearing; the comments say why.
- `dbt/macros/generate_schema_name.sql` — clean schema names (`marts`, not
  `main_marts`). Six lines, and every schema reference in the project depends on it.
- `dbt/profiles.yml` — rename the profile, keep the `env_var('WAREHOUSE_PATH', …)`
  pattern. **Add `ci` and `prod` targets if you are moving off DuckDB**, and
  that is the one place this project's shape does not carry over. There is a
  single target here because the environment *is* the file: `WAREHOUSE_PATH`
  swaps the whole database, so a second output would differ in name only. On
  Snowflake, BigQuery or Postgres the *schema* is the environment, which is
  exactly what a target separates, so the reasoning written beside that output
  inverts and `generate_schema_name.sql` below has to be reconsidered with it.
- `orchestration/resources.py` — the dbt/dlt resource handles.
- `.github/workflows/ci.yml` and `nightly.yml` — the offline-fixtures /
  live-sources split holds whatever you're ingesting.
- `docs/STYLE_GUIDE.md`.

### Adapt — the structure holds, the specifics don't

- `publish/build_report.py` — `TABLE_TO_DBT_MODEL` and `TABLE_TO_ASSET_KEY`, the
  two maps that give the Evidence site one dependency per table it reads. **Empty
  the first before anything else loads**: `orchestration/assets.py` resolves each
  mapped model at import time, so a stale entry stops the whole graph with
  `RuntimeError: generator raised StopIteration`, naming no model. The four
  `pipeline_*` entries in the second are generic.
- `scripts/record_fixtures.py` — 333 lines, almost all per-source trimming; one
  untrimmed CSV needs about thirty.
- `orchestration/assets.py` — **more than half of it is the example**: on the
  dry run it went from 801 lines (at `c054e53`) to 352. What carries over is
  `RawSchemaDltTranslator` (§2 says why it matters; it stays here rather than in
  the package because it's twenty lines wrapped around two of that module's
  constants), `FolderGroupDbtTranslator`, the freshness policies, the unpartitioned
  `raw_assets`, `dbt_models`, `pipeline_status`, `evidence_site`, and the two wiring
  checks `run_history_records_this_build` and `site_pages_all_rendered`.
  - **Every other asset check is yours**, and so are the year- and
    month-partitioned blocks and `RAW_DESCRIPTIONS`' entries.
  - **One generic piece reaches through the example.** `pipeline_status`
    depends on the two Polars assets; with none, depend on
    `list(dbt_models.keys)`, because `deps=[dbt_models]` is refused.
- `orchestration/definitions.py` — **three jobs, not two.** `full_refresh` (without
  the site) and `publish_site` (with it) are about Node and carry over.
  `load_retail` and its selection exist only because retail is month-partitioned
  where WDI is yearly, and `just materialize` / `just materialize-site` run it by
  name. The `assets=` and `asset_checks=` lists name every example asset.
- `publish/export_warehouse.py` — not only constants. `PUBLISHED_SCHEMAS` and
  `ATTRIBUTION` are config. **`release_notes()` is the example's release body
  written inside the function**: its grain exceptions, retail, FX and CBAM
  notes, and `marts.fct_emissions_energy` as the sample table. Keep its
  scaffolding (the query snippets, the asset table, the additivity and
  reader-version notes) and replace the prose. `_history()` queries
  `marts.fct_co2_estimate_versions` and returns `None` without it.
- `.github/workflows/pages.yml` and `release-data.yml` — paths and the basePath
  step are generic; the snapshot carry-forward only matters if you have a
  snapshot. `release-data.yml`'s verify step checks rows in three of the example's
  relations and one Parquet file by name, and requires a lakehouse asset in every
  release (§7). The one part to re-derive rather than copy is `pages.yml`'s `paths:`
  allowlist, which names this repo's directories, and `tests/test_workflows.py`
  with it, since that is what stops the allowlist drifting from your tree.
- `reports/sources/warehouse/connection.yaml` — the relative path to the DuckDB
  file, nothing else.
- **The tests are mostly plumbing tests wired to the example's content.**
  `test_ingest.py` and `test_transform.py` are yours to delete. Nearly every other
  file mixes the two:
  - `test_report.py` asserts `marts.fct_emissions_energy` is among the tables the
    pages read;
  - `test_lakehouse.py` names the weather table;
  - `test_export.py` and `test_fixtures.py` both read `tests/pipeline_urls.py`,
    the list of every URL the pipeline builds, which imports every source
    module and is yours to rewrite;
  - `test_asset_checks.py` is three quarters the six domain checks;
  - `test_privacy.py`, `test_additivity.py`, `test_definitions.py`,
    `test_bus_matrix.py` and `test_exposures.py` each hold a few pytest cases
    that pin the example's models, pages or retail identifier.

  On a new project, delete those cases and keep the rest. A mechanism test that
  reads a project allowlist patches it to its own fixture, as the lakehouse
  cases in `test_export.py` and `test_restore_history.py` do, or it goes vacuous
  when the allowlist is empty.
- **The prose guards are calibrated to this repo's volume of prose.**
  - `test_documented_counts.py` floors its scans: the count-claim scanner must
    find more than 35 claims, the additivity one at least 8. Two of its cases require a specific
    claim (the description coverage, and a `PASS=` line from the course).
    `CITED_MODELS` names the example's models.
  - `test_course.py` always includes the course index, so its skill-citation
    cases crash with no course.
  - Remove the floors and the claim-must-exist cases; the stale-claim checks
    themselves carry over and are worth keeping.

### Delete — this is the example, not the framework

All of `ingest/sources/`: one module per publisher, and every one of them is
this example's domain.

Keep `ingest/http.py`, and keep `ingest/pipeline.py` as coordination:
- **Keep** `load_groups`, `build_pipeline`, `pipeline_name`, `REFRESH` and
  `PIPELINE_DATASET` (which `publish/restore_history.py` reads).
- **Keep, emptied:** the `FULL_REFRESH_RESOURCES`, `INCREMENTAL_RESOURCES` and
  `PARTITIONED_RESOURCES` tuples, which `orchestration/assets.py` imports.
- **Keep, with its resource list emptied:** the `@dlt.source` function and
  `main()`, which `python -m ingest.pipeline` needs.

Then delete:
- everything under `dbt/models`, `dbt/seeds`, `dbt/snapshots` and `dbt/tests`.
  Leave a `.gitkeep` in the first three: git tracks no empty directory, and
  `just lint` fails on `models` or `snapshots` missing
  (`Specified path does not exist`);
- both `transform/co2_intensity.py` and `transform/retail_rfm.py`;
- three of the four `scripts/`, everything but `record_fixtures.py`;
- `tests/fixtures/`;
- all of `reports/pages` and `reports/sources/warehouse/*.sql` **except
  `pipeline.md` and the four `pipeline_*.sql` queries**, which render the
  observability tables and name nothing in the example;
- the eight justfile recipes that exist only for the example (`ingest-wdi-full`,
  `backfill-*`, `disclosure-risk`, `course-*`), and the two transform lines in
  `transform` and `test-pipeline`;
- `docs/course/`, and seven of the eighteen skills: country stats, compliance,
  retail, currency and calendar, weather, unit-tested models, course authoring;
- the docs about this warehouse's data. What stays is `STYLE_GUIDE.md`,
  `ORCHESTRATION.md`, `RUNNING_AS_A_SERVICE.md`, and `WAREHOUSE.md` for its bus
  matrix block;
- about a quarter of `AGENTS.md` (§9).

Several of the files that survive unchanged still name the example in comments
and prose: `reports/README.md`, `docs/STYLE_GUIDE.md`, `querying-the-warehouse`.
Passing the guards is not the same as being generic.

## 2. The names that join the layers

**The layers are wired by string, not by import.** Every mismatch below fails
*silently*: the pipeline runs, the graph renders, and something is disconnected
or stale.

| Name | Set in | Must match |
|------|--------|------------|
| dlt resource `name=` | `ingest/sources/<source>.py` | the `raw` table name, and the dbt source's `name:` |
| dbt source `name:` | `dbt/models/staging/_sources.yml` | the dlt resource name |
| Dagster key `raw/<resource>` | `RawSchemaDltTranslator` | the key `dagster-dbt` derives from `_sources.yml` |
| mart table name | `dbt/models/marts/*.sql` | the `<schema>.<table>` in the Evidence source queries |
| `<schema>.<table>` in a source query | `reports/sources/**/*.sql` | a key in `TABLE_TO_DBT_MODEL` or `TABLE_TO_ASSET_KEY` |
| Evidence connection `name:` | `reports/sources/*/connection.yaml` | the source directory name, and `${name.query}` in pages |
| the DuckDB **file stem** | wherever the file is written | the catalog dbt bakes into view SQL for its own refs (`warehouse.staging.x`) |
| the DuckLake **attach alias** | `profiles.yml`, `lake.lakehouse.ATTACH_ALIAS` | `_sources.yml`'s `database:`, which dbt bakes into every staging view (`lakehouse.raw.x`) |

**The asset key is the only join between EL and T.** `raw/<resource>` from the dlt
side, `raw/<source table>` from the dbt side. Get it wrong and both halves still
materialize, side by side, unconnected, with no error anywhere, and
`dagster definitions validate` passes too. Only the graph shows it, so look at
the graph every time you add or rename a resource.

**Two names become catalog names in stored SQL.** dbt writes views with
fully-qualified SQL: a staging view reads its source as `lakehouse.raw.owid_co2`
and a view over a model as `warehouse.staging.stg_co2`, so both the attach alias
and the file stem are fixed the day the first view is built. A template can vary
the project name; it cannot make either of these a variable. Rename the file, or `ATTACH … AS wh`, and the views
raise `Catalog "warehouse" does not exist` while the tables keep working, a
half-broken artifact that looks fine until someone queries staging. Pick the file
name once, and pin it with a test if you publish the file.

## 3. Four decisions to make before writing code

### The grain

Here it's `(country_iso3, year)`, and most of the warehouse follows from it: the
`unique_combination_of_columns` contract on every fact-shaped model, the spine,
the join key in every mart model.

Write yours down as `(entity, period)` in the style guide before you build the
first staging model. Then hold every staging model to it. When a source publishes
at a different grain, model it at *its* grain and derive the project grain from
that rather than flattening at the edge. This repo does it once, for Eurostat's
half-years, because the averaging step that reaches the annual grain destroys
real signal, so both grains are modelled and the docs say which one to chart.

### What decides an entity exists

`stg_country`, plus a `country_overrides.csv` seed for the ones the World Bank
omits. The mart's spine is that dimension × the year range, and facts join *onto*
the spine rather than the spine being inferred from whichever fact you happened
to start from.

Without an explicit dimension your mart's population is set by whichever source
you left-joined from, and coverage gaps become silently dropped rows instead of
rows with nulls. Build the dimension first, even if it's a seed file.

It holds for a grain with no country in it. On the dry run a `dim_month` spine
under monthly gold prices gave the fact one more row than its source: the current
month, which the publisher had not priced yet, as a null. **Name the fact's key
after the dimension's**: `modern_data_stack.bus_matrix` marks a fact as conforming
when it carries a column with the dimension's single-column unique key *name*, so
a fact keyed `price_month` does not conform to `dim_month.month_start`.

### Which resource is incremental

Default to `write_disposition="replace"` and `refresh="drop_resources"`. Reach for
`merge` only when a full pull is expensive, and take the whole package with it,
all five of:

1. a primary key that really is the grain,
2. declared `columns={...}` types, because the schema is no longer re-inferred and
   dlt only ever *widens*,
3. a **lookback window**, not a high-water mark, if the publisher restates
   published periods,
4. per-key watermarks if the resource is a union of series, so a newly added key
   still pulls its full history,
5. a separate `run()` call, because `refresh` is an argument to the run and not a
   property of the resource: one refreshing call would drop the incremental
   table and its watermark along with it.

### What is state rather than a build artifact

If any part of your warehouse can't be recomputed from the sources (a dbt
snapshot is the usual case), decide on day one how it survives. Every workflow
builds from an empty file, so without a carry-forward step every published copy
holds one version per row forever and looks broken. `publish/restore_history.py`
is the shape: download the previous release, copy the schema in *before* the graph
runs, and verify the result is no smaller than what went in.

Also decide how far a snapshot reaches. This one is deliberately two columns and
one era, because it's the table `rm data/warehouse.duckdb` destroys for good.

## 4. Invariants that fail silently

`AGENTS.md` and the skills under `.agents/skills/` have the full list for this
project. These are the ones that recur in
anything built this way:

- **`WAREHOUSE_PATH` must be absolute.** dbt resolves it from `dbt/`, the Python
  layers from the project root. A relative override gives you two different
  warehouses and no error. Every layer here gets the answer from
  `modern_data_stack.paths` so they can't disagree. It used to come from a
  `REPO_ROOT` in `ingest/pipeline.py` that meant "the parent of `ingest/`", which
  made the landing zone and the exporter depend on where the *ingestion* layer
  sat.
- **`dbt deps` before `dbt build`, `dbt parse` *or* `sqlfluff`.** `dbt_packages/`
  and `target/` are gitignored, and `prepare_if_dev()` only fires under
  `dagster dev`. Every workflow has to run it explicitly.
- **dlt state is keyed on the pipeline *name*, not the destination.** A fixture run
  hands its watermarks to the next real run unless the name differs.
- **A recorded fixture can be ignored by git and pass everywhere but CI.**
  `.gitignore` carries `*.csv` with exceptions for the seeds and both kinds of
  test fixture. Keep all three. Without the ingest one, a new source with a
  plain CSV fixture records it, passes `just test-pipeline` in the working tree,
  and `git add -A` silently leaves the file out; a clean checkout then fails
  with `No such file or directory`. A new directory of CSVs needs its own line.
- **Evidence's build state can go stale after a column change.** `just report`
  has validated a page against a dropped column's old schema; clearing
  `reports/.evidence/` (`just report-clean`) after any mart change fixes it.
- **`evidence build` exits 0 for a site missing a page.** Check rendered file
  *size*, not exit status: the pages here render at over 20 kB and the check's
  floor is 8 kB, which catches a route that emitted nothing but the framework shell.
- **A column named `tests` or `rows` silently draws no bars** in an Evidence chart.
  No error, no warning, and the same column is fine in a table three lines below.
- **Assets must be listed explicitly in `Definitions`.** `definitions validate`
  passes happily while a new asset is absent from the graph entirely.

## 5. Renaming the project

**The name is three names in one string**: the Python package
(`src/modern_data_stack/`, imported by 36 files), the dbt project and profile,
and the dlt pipeline name (which also names dlt's state directory). The package
is decoupled from the other two, so renaming the project need not touch it — but
only because of one key, below. The dry run renamed all three to
`gold_warehouse`; on the tree left after §1's deletes that was **63 files**, 12
moves and 51 edits.

- **The distribution name and the package are decoupled by one key**, which
  this repo now sets: `[tool.uv.build-backend] module-name = "modern_data_stack"`
  in `pyproject.toml`. Delete it and uv_build derives the module from the project
  name again, so `uv sync` fails with `Expected a Python module at:
  src/<new_name>/__init__.py` — **the first time anyone renames the project, and
  not before**. A coupling that only fails years later in someone else's fork is
  why `tests/test_packaging.py` holds the key rather than trusting it.
- **Renaming the project** (keeping the package) touches:
  - `pyproject.toml`: `[project] name` and `[tool.dagster] code_location_name`;
  - `uv.lock`, rewritten by `uv sync`;
  - `dbt/dbt_project.yml`: `name:`, `profile:` and the `models:`, `seeds:` and
    `snapshots:` keys, all five of them the project name;
  - `dbt/profiles.yml`'s top-level key;
  - `ingest/pipeline.py`'s `pipeline_name`. Renaming it resets dlt's state,
    which is what you want on a fresh project and not what you want later;
  - the `just dlt-state` default;
  - `tests/test_lakehouse.py`, which reads the profile by that key;
  - `tests/test_restore_history.py`'s state directory names;
  - `reports/package.json` and its lockfile (cosmetic).
- **Renaming the package** as well moves `src/` and rewrites every import —
  including two places a scan of `*.py` misses: `pyproject.toml`'s
  `[project.scripts]` entry point (`mds = "modern_data_stack:main"`, which
  nothing validates at install time, so it fails the first time someone runs the
  script) and the inline Python in `.github/workflows/release-data.yml`'s
  heredocs, which import the package and run nowhere but a release. Expect the
  first pre-commit run afterwards to re-sort imports in files the rename did not
  otherwise touch: ruff orders first-party imports alphabetically, so the
  package's position among `ingest`, `lake` and `publish` moves with its name.

`.claude/`'s plugin marketplace is also named `modern-data-stack`. That is
tooling identity, not project identity, and nothing breaks if it keeps the name.

## 6. Build order

Each step leaves the repo runnable, so a failure has one plausible cause.

1. **Skeleton.** Copy the tree, delete the example files listed in §1, rename per
   §5 — or start from the template above, which is steps 1–6 done, keeping the
   fixtures and the observability of §7 and leaving out its snapshots and
   publishing. `just setup` succeeding says only that the dependencies resolve:
   it imports nothing from the project, and it passed on the dry run while seven of
   CI's eight steps failed. The useful gate is `just dbt-parse && dagster
   definitions validate -m orchestration.definitions`. **An empty skeleton
   cannot pass the test suite**: with no resource dlt never creates the
   catalog, and `transform.pipeline_status` fails to open it. So take step 1
   straight into step 2.
2. **One source, end to end.** One dlt resource → `raw` → one staging model → a
   trivial mart → one Evidence chart. Resist adding the second source until the
   first has reached a page; the seams in §2 are all exercised by that path and
   nothing else finds them.
3. **The dimension and the spine**, before the second fact source. Retrofitting a
   spine means rewriting every join you already wrote.
4. **Fixtures and CI.** The *first* fixture belongs to step 2: CI runs with
   `INGEST_FIXTURES=1`, so the first source cannot pass `just test-pipeline`
   without one. What waits for this step is the rest of CI (the workflows, the
   nightly), before there are five sources. Recording fixtures for one endpoint is
   a morning; for five it's a project.
5. **Tests as grain contracts.** `unique_combination_of_columns` on every
   fact-shaped model, the day it's created. It's how the grain from §3 stops
   being a convention.
6. **Dagster.** Once two layers exist, so there's an edge to get wrong.
7. **The optional layers** (§7), in whatever order earns its keep.

## 7. What to drop if you want less

Four layers are optional, and independent of each other. **The landing zone
(`lake/`) is no longer one of them.** It used to be a hive-partitioned Parquet
archive written *beside* the warehouse, droppable for anyone who did not want
cross-run diffability, and it is now the DuckLake catalog `raw` actually lives
in. dbt attaches it and every staging model reads through it, so dropping it is
a decision about where dlt lands rather than a layer you leave out. The
diffability argument went with the old format too: DuckLake content-addresses
its files, so a diff of the *files* no longer means anything and `revisions()`
compares two snapshots instead.

- **Snapshots** (`dbt/snapshots/`, `publish/restore_history.py`) — only if your
  publishers restate. If they don't, this layer records nothing.
- **Publishing** (`publish/export_warehouse.py`, `release-data.yml`) — only if
  someone consumes the data without running the pipeline. Note that it turns
  "we use public data" into "we redistribute public data", which is an attribution
  obligation. Two things in it assume this repo's data:
  - **The export refuses to run with nothing classified as personal data**
    (`PolicyError: no columns are classified as direct_identifier — refusing to
    publish`). The refusal is deliberate: "nothing classified" and "nothing to
    classify" look the same to it. A project with no personal data has to make
    `prepare_published_copy` skip `pseudonymise` explicitly.
  - **`release-data.yml` requires a lakehouse asset in every release.** A project
    whose `PUBLISHED_TABLES` is empty writes none, so the check has to depend on
    that allowlist.
- **Pipeline observability** (`transform/pipeline_status.py`) — earns its place
  once there are enough tables that "is anything stale?" isn't answerable by eye.
- **Fixtures and the nightly job** (`tests/fixtures/`, `nightly.yml`) — the one I'd
  drop last. Without it, a red CI build doesn't distinguish "we broke it" from
  "the API is down", and that ambiguity is what trains people to re-run failed builds.

The stack itself is less separable: Dagster is additive (`just run` still works
without it), but dlt, DuckDB, dbt and Evidence each assume the previous one.

## 8. Where this shape stops being the right one

Every number below is measured on this warehouse today, not estimated. The
dollar figure for running it is zero — it is a file on a laptop — so the useful
question is not what it costs but which layer gives first, and the answer is not
the one people reach for.

**What it holds now**, measured 2026-09-09 alongside the figures in
`FOR_REVIEWERS.md` §3, which this agrees with by construction. 3.7M rows across
the modelled layers in a 282 MB DuckDB file, plus a 111 MiB DuckLake landing
zone — that one grows about 39 MiB per full ingest and nothing expires the
snapshots, which is its own answer to what a run costs. The largest relation is
`fct_retail_order_line` at 1,067,371 rows. A full `dbt build` — 571 nodes, 33
models, 482 data tests, 36 unit tests — takes **24.5 s** of dbt's own time on
four threads.
`analytics.pipeline_runs` records that per build, so the trend is a query rather
than a memory.

**Which layer gives first is answered in full by
[`docs/FOR_REVIEWERS.md` §4](FOR_REVIEWERS.md#4-what-breaks-at-1000), and this
section deliberately does not restate it.** That one is written for someone
assessing this repo and carries the ordered failure list, the `quack` extension
measurement and the per-model materialisation argument. What belongs *here* is
the porting decision the ordering implies, which is shorter than the mechanism:

- **The single-writer lock is the one to plan around, and it is not a scale
  limit.** One writer xor many readers binds at 43k rows exactly as hard as at
  43M. If your project has a second writer — a second pipeline, a second team, a
  reverse-ETL job — you need a server-based warehouse on day one, and no row
  count will tell you that.
- **The first volume limit is whatever materialises a whole relation in
  memory**, which here is the two Polars transforms. Note *which* relation: they
  read a mart, not a landing table, so they are bounded by what dbt already
  aggregated — 43,138 and 5,881 rows against a 1.07M-row source. Porting this
  shape, the equivalent question is what your heavy-transform layer reads, not
  how much you ingest.
- **The file itself is not the constraint.** DuckDB handles far more than this
  on one machine; what one file cannot do is have two writers, scale
  horizontally, or be restored by anything but a copy.

**What to do about it, in order.** Nothing, until a second writer exists — then
a real warehouse, and `dbt/profiles.yml` grows the targets §1 says it should.
The layer that changes is the profile and the two Polars files; the models, the
tests, the contracts, the exposures and the release all port unchanged, which is
the argument for the shape rather than for the file.

## 9. What doesn't transfer

- **The gotchas that are about the sources**, and there are a lot of them here:
  padded region names, ISO2 exceptions, per-metric coverage curves, which GDP
  series to divide by. Your sources have an equally long list and it will be
  completely different. The transferable part is the *habit* of writing them down
  next to the code, in the terms the next reader will hit them.
- **The data-quality thresholds.** Every bound in this project was calibrated
  against the real distribution, and several are deliberately absent because the
  honest range would make the test pass everything. Copying a threshold is copying
  someone else's data.
- **The Evidence pages.** Layout ideas travel; queries don't.
- **`AGENTS.md`.** The structure travels (schemas, conventions, gotchas, the
  per-layer sections), and about a quarter of the content is specific enough to
  delete: cutting the warehouse schemas, snapshot history, personal data, the
  course and the country-year conventions took it from about 30 KB to 22 KB.
  Keep the sections about tooling (the sqlfluff pin, the ruff defaults, dependabot,
  the `dbt deps` prerequisite); those are the same on any project using them.
  `CLAUDE.md` is an `@AGENTS.md` import plus this repo's Claude Code plugins:
  keep the import and the `.claude/skills` symlink, rewrite the rest.

To *share* `src/modern_data_stack/` between projects rather than copying it, add
this repo as a git dependency and delete the copy. Nothing in the package imports
the layers above it, so that works today, but copying is the better default
until you have two projects that actually disagree about something. A shared
package with one consumer is just a longer import path.
