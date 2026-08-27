# Reusing this stack

How to start a *new* project on this shape — dlt → DuckDB → dbt → Polars →
Evidence, orchestrated by Dagster — using this repo as the reference
implementation.

This is not a checklist for adding a source to *this* warehouse; that's
[`.claude/skills/adding-a-data-source`](../.claude/skills/adding-a-data-source/SKILL.md).
It's the layer above: what carries over to a different dataset, what has to be
rewritten, and the handful of decisions that are expensive to change later.

**Most of what makes this repo work is not transferable code.** The pipeline is
~4,000 lines and the part with nothing domain-specific in it is maybe a third —
the layout, the wiring conventions, the CI shape, the lint config. The rest is a
worked example. The fastest way to reuse it is to copy the tree, keep the
skeleton and delete the emissions.

That third is already separated out, so you don't have to go looking for it:
it's `src/modern_data_stack/`, it takes its configuration as arguments, and the
project modules that call it hold the constants.

## 1. What you're actually reusing

### The package — `src/modern_data_stack/`

Six modules, no mention of emissions in any of them. Copy the directory, or
depend on it and write only the layers below.

| Module | What it does | Configured by |
|--------|--------------|---------------|
| `paths` | project root, warehouse file, lake dir, dbt manifest | `PROJECT_ROOT`, `WAREHOUSE_PATH`, `LAKE_DIR` |
| `fixtures` | serve recorded payloads instead of live endpoints | a list of `(url pattern, filename)` routes |
| `lake` | hive-partitioned Parquet archive of warehouse tables | a table tuple and a partition column |
| `observability` | dlt/dbt/DuckDB metadata as queryable tables | landing-table and layer names |
| `export` | package a warehouse as a publishable artifact | schemas, attribution, a notes renderer |
| `history` | carry a dbt snapshot forward between builds | the snapshot schema name |

Each project module keeps the entry point, so `python -m lake.lakehouse`, the
justfile recipes and the asset graph all still call the same names.

### Config-only — copy the file, change the constants at the top

- `ingest/fixtures.py` — the `_ROUTES` table.
- `lake/lakehouse.py` — where the DuckLake landing zone lives, `PUBLISHED_TABLES`
  and the merge keys.
- `transform/pipeline_status.py` — `SOURCE_TABLES` and `LAYERS`.
- `scripts/export_warehouse.py` — `PUBLISHED_SCHEMAS`, `ATTRIBUTION`, the release
  notes and whatever your manifest wants that the generic one can't know.
- `scripts/restore_history.py` — the schema name and the CLI.

### Copy verbatim — the tooling

- `.sqlfluff` — retarget `dialect` if you're not on DuckDB; everything else holds.
- `.pre-commit-config.yaml` — including the reasons the sqlfluff hook is `local`.
- `pyproject.toml`'s `[tool.ruff]`, `[tool.ruff.lint]` and
  `[tool.pytest.ini_options]` blocks. The `extend-select` list and
  `combine-as-imports` are both load-bearing; the comments say why.
- `dbt/macros/generate_schema_name.sql` — clean schema names (`marts`, not
  `main_marts`). Six lines, and every schema reference in the project depends on it.
- `dbt/profiles.yml` — rename the profile, keep the `env_var('WAREHOUSE_PATH', …)`
  pattern.
- `orchestration/resources.py` and `orchestration/definitions.py` — the dbt/dlt
  resource handles and the two-job split (`full_refresh` without the site,
  `publish_site` with it). Both are about Node, not about your data.
- `.github/workflows/ci.yml` and `nightly.yml` — the offline-fixtures /
  live-sources split holds whatever you're ingesting.
- `docs/STYLE_GUIDE.md`.

### Adapt — the structure holds, the specifics don't

- `scripts/build_report.py` — `TABLE_TO_DBT_MODEL` and `TABLE_TO_ASSET_KEY`, the
  two maps that give the Evidence site one dependency per table it reads.
- `scripts/record_fixtures.py`.
- `orchestration/assets.py` — the asset shapes carry over almost unchanged, and
  `RawSchemaDltTranslator` is the piece worth copying by hand (§2 says why it
  matters; it stays here rather than in the package because it's twenty lines
  wrapped around two of that module's constants). Every asset *check* is yours.
- `.github/workflows/pages.yml` and `release-data.yml` — paths and the basePath
  step are generic; the snapshot carry-forward only matters if you have a
  snapshot. The one part to re-derive rather than copy is `pages.yml`'s `paths:`
  allowlist, which names this repo's directories — and `tests/test_workflows.py`
  with it, since that is what stops the allowlist drifting from your tree.
- `reports/sources/warehouse/connection.yaml` — the relative path to the DuckDB
  file, nothing else.
- `tests/test_lake.py`, `test_export.py`, `test_report.py` — structural tests over
  the plumbing, not over the numbers. `test_ingest.py` and `test_transform.py` are
  yours.

### Delete — this is the example, not the framework

`ingest/pipeline.py` (keep `_get_json`, `load_groups`, `build_pipeline` and the
`REFRESH` constant; everything else is six sources), all of `dbt/models`,
`dbt/seeds` and `dbt/snapshots`, `transform/co2_intensity.py`, all of
`reports/pages` and `reports/sources/warehouse/*.sql`, `tests/fixtures/`,
and roughly half of `CLAUDE.md`.

## 2. The names that join the layers

**The layers are wired by string, not by import.** Every mismatch below fails
*silently*: the pipeline runs, the graph renders, and something is disconnected
or stale.

| Name | Set in | Must match |
|------|--------|------------|
| dlt resource `name=` | `ingest/pipeline.py` | the `raw` table name, and the dbt source's `name:` |
| dbt source `name:` | `dbt/models/staging/_sources.yml` | the dlt resource name |
| Dagster key `raw/<resource>` | `RawSchemaDltTranslator` | the key `dagster-dbt` derives from `_sources.yml` |
| mart table name | `dbt/models/marts/*.sql` | the `<schema>.<table>` in the Evidence source queries |
| `<schema>.<table>` in a source query | `reports/sources/**/*.sql` | a key in `TABLE_TO_DBT_MODEL` or `TABLE_TO_ASSET_KEY` |
| Evidence connection `name:` | `reports/sources/*/connection.yaml` | the source directory name, and `${name.query}` in pages |
| the DuckDB **file stem** | wherever the file is written | the catalog dbt bakes into view SQL (`warehouse.raw.x`) |

**The asset key is the only join between EL and T.** `raw/<resource>` from the dlt
side, `raw/<source table>` from the dbt side. Get it wrong and both halves still
materialize, side by side, unconnected, with no error anywhere —
`dagster definitions validate` passes too. Only the graph shows it, so look at
the graph every time you add or rename a resource.

**The DuckDB file stem becomes a catalog name.** dbt writes staging views with
fully-qualified SQL, so `warehouse.duckdb` produces views that say
`warehouse.raw.owid_co2`. Rename the file, or `ATTACH … AS wh`, and the views
raise `Catalog "warehouse" does not exist` while the tables keep working — a
half-broken artifact that looks fine until someone queries staging. Pick the file
name once, and pin it with a test if you publish the file.

## 3. Four decisions to make before writing code

### The grain

Here it's `(country_iso3, year)`, and most of the warehouse follows from it: the
`unique_combination_of_columns` contract on every fact-shaped model, the spine,
the lake's partition column, the join key in every mart.

Write yours down as `(entity, period)` in the style guide before you build the
first staging model. Then hold every staging model to it. When a source publishes
at a different grain, model it at *its* grain and derive the project grain from
that rather than flattening at the edge. This repo does it once, for Eurostat's
half-years, because the averaging step that reaches the annual grain destroys
real signal — so both grains are modelled and the docs say which one to chart.

### What decides an entity exists

`stg_country`, plus a `country_overrides.csv` seed for the ones the World Bank
omits. The mart's spine is that dimension × the year range, and facts join *onto*
the spine rather than the spine being inferred from whichever fact you happened
to start from.

Without an explicit dimension your mart's population is set by whichever source
you left-joined from, and coverage gaps become silently dropped rows instead of
rows with nulls. Build the dimension first, even if it's a seed file.

### Which resource is incremental

Default to `write_disposition="replace"` and `refresh="drop_resources"`. Reach for
`merge` only when a full pull is expensive, and take the whole package with it —
all five of:

1. a primary key that really is the grain,
2. declared `columns={...}` types, because the schema is no longer re-inferred and
   dlt only ever *widens*,
3. a **lookback window**, not a high-water mark, if the publisher restates
   published periods,
4. per-key watermarks if the resource is a union of series, so a newly added key
   still pulls its full history,
5. a separate `run()` call, because `refresh` is an argument to the run and not a
   property of the resource — one refreshing call would drop the incremental
   table and its watermark along with it.

### What is state rather than a build artifact

If any part of your warehouse can't be recomputed from the sources — a dbt
snapshot is the usual case — decide on day one how it survives. Every workflow
builds from an empty file, so without a carry-forward step every published copy
holds one version per row forever and looks broken. `scripts/restore_history.py`
is the shape: download the previous release, copy the schema in *before* the graph
runs, and verify the result is no smaller than what went in.

Also decide how far a snapshot reaches. This one is deliberately two columns and
one era, because it's the table `rm data/warehouse.duckdb` destroys for good.

## 4. Invariants that fail silently

`CLAUDE.md` has the full list for this project. These are the ones that recur in
anything built this way:

- **`WAREHOUSE_PATH` must be absolute.** dbt resolves it from `dbt/`, the Python
  layers from the project root. A relative override gives you two different
  warehouses and no error. Every layer here gets the answer from
  `modern_data_stack.paths` so they can't disagree — it used to come from a
  `REPO_ROOT` in `ingest/pipeline.py` that meant "the parent of `ingest/`", which
  made the lake and the exporter depend on where the *ingestion* layer sat.
- **`dbt deps` before `dbt build`, `dbt parse` *or* `sqlfluff`.** `dbt_packages/`
  and `target/` are gitignored, and `prepare_if_dev()` only fires under
  `dagster dev`. Every workflow has to run it explicitly.
- **dlt state is keyed on the pipeline *name*, not the destination.** A fixture run
  hands its watermarks to the next real run unless the name differs.
- **DuckDB's `COPY … (overwrite true)` only replaces the partitions it writes.** A
  partition whose last row disappeared upstream keeps answering from a stale file.
  Delete the directory first.
- **Evidence caches each source's schema keyed on the source SQL.** A `select *`
  that gained a column looks unchanged, and validation fails against the stale
  schema — hence `just report-clean` after any mart change.
- **`evidence build` exits 0 for a site missing a page.** Check rendered file
  *size*, not exit status: the pages here render at 17–74 kB and the check's floor
  is 8 kB, which catches a route that emitted nothing but the framework shell.
- **A column named `tests` or `rows` silently draws no bars** in an Evidence chart.
  No error, no warning, and the same column is fine in a table three lines below.
- **Assets must be listed explicitly in `Definitions`.** `definitions validate`
  passes happily while a new asset is absent from the graph entirely.

## 5. Renaming the project

Six files, and the dbt profile name has to match in two of them:

- `pyproject.toml` — `[project] name`, `[project.scripts]`, `[tool.dagster]
  code_location_name`
- `src/<package>/` — the directory and its `__init__.py`
- `dbt/dbt_project.yml` — `name:`, `profile:`, and the two `models:`/`snapshots:`
  keys, all four of which are the project name
- `dbt/profiles.yml` — the top-level key, which must equal `dbt_project.yml`'s
  `profile:`
- `ingest/pipeline.py` — the dlt `pipeline_name` (renaming this resets dlt's state,
  which is what you want on a fresh project and not what you want later)
- `reports/package.json` — cosmetic

## 6. Build order

Each step leaves the repo runnable, so a failure has one plausible cause.

1. **Skeleton.** Copy the tree, delete the example files listed in §1, rename per
   §5. `just setup` should succeed with an empty pipeline.
2. **One source, end to end.** One dlt resource → `raw` → one staging model → a
   trivial mart → one Evidence chart. Resist adding the second source until the
   first has reached a page; the seams in §2 are all exercised by that path and
   nothing else finds them.
3. **The dimension and the spine**, before the second fact source. Retrofitting a
   spine means rewriting every join you already wrote.
4. **Fixtures and CI.** As soon as the first source lands, before there are five.
   Recording fixtures for one endpoint is a morning; for five it's a project.
5. **Tests as grain contracts.** `unique_combination_of_columns` on every
   fact-shaped model, the day it's created. It's how the grain from §3 stops
   being a convention.
6. **Dagster.** Once two layers exist, so there's an edge to get wrong.
7. **The optional layers** (§7), in whatever order earns its keep.

## 7. What to drop if you want less

Five layers are optional, and independent of each other:

- **The lake** (`lake/`) — drop it unless you want cross-run diffability or you
  intend to move to object storage. It's the cheapest to add later.
- **Snapshots** (`dbt/snapshots/`, `scripts/restore_history.py`) — only if your
  publishers restate. If they don't, this layer records nothing.
- **Publishing** (`scripts/export_warehouse.py`, `release-data.yml`) — only if
  someone consumes the data without running the pipeline. Note that it turns
  "we use public data" into "we redistribute public data", which is an attribution
  obligation.
- **Pipeline observability** (`transform/pipeline_status.py`) — earns its place
  once there are enough tables that "is anything stale?" isn't answerable by eye.
- **Fixtures and the nightly job** (`tests/fixtures/`, `nightly.yml`) — the one I'd
  drop last. Without it, a red CI build doesn't distinguish "we broke it" from
  "the API is down", and that ambiguity is what trains people to re-run failed builds.

The stack itself is less separable: Dagster is additive (`just run` still works
without it), but dlt, DuckDB, dbt and Evidence each assume the previous one.

## 8. What doesn't transfer

- **The gotchas that are about the sources**, and there are a lot of them here —
  padded region names, ISO2 exceptions, per-metric coverage curves, which GDP
  series to divide by. Your sources have an equally long list and it will be
  completely different. The transferable part is the *habit* of writing them down
  next to the code, in the terms the next reader will hit them.
- **The data-quality thresholds.** Every bound in this project was calibrated
  against the real distribution, and several are deliberately absent because the
  honest range would make the test pass everything. Copying a threshold is copying
  someone else's data.
- **The Evidence pages.** Layout ideas travel; queries don't.
- **`CLAUDE.md`.** The structure travels — schemas, conventions, gotchas, the
  per-layer sections — and about half the content is specific enough to delete.
  Keep the sections about tooling (the sqlfluff pin, the ruff defaults, dependabot,
  the `dbt deps` prerequisite); those are the same on any project using them.

To *share* `src/modern_data_stack/` between projects rather than copying it, add
this repo as a git dependency and delete the copy. Nothing in the package imports
the layers above it, so that works today — but copying is the better default
until you have two projects that actually disagree about something. A shared
package with one consumer is just a longer import path.
