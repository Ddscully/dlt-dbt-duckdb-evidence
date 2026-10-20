# 0023. Assets and checks register by living in `orchestration/defs/`, not by a list

Status: proposed; becomes `accepted YYYY-MM-DD (#PR)` when it merges

## Context

`orchestration/definitions.py` listed every asset and asset check by hand, and
`dg.Definitions` treats an omission as nothing at all: the asset is not in the
graph, `AssetSelection.all()` never sees it, and `just validate` passes.
`raw/retail_invoice_lines`, `analytics/retail_rfm` and two checks sat
unregistered that way until `full_refresh` failed in CI one layer downstream, in
dbt, naming the symptom. A test then compared what `orchestration/assets.py`
defined with what the graph resolved, and the `dagster-graph-and-jobs` skill
called the `defs/` folder "separable, and still not taken", on the grounds that
the test was cheap and documented two traps of its own.

The module had grown to 778 lines holding four layers and the checks, and
ADR 0022 had just moved the recipes to `dg`, whose scaffold is a `defs/` folder.
Measured on dagster 1.13.26:

- **`dg.load_from_defs_folder` works on this layout.** It reads `[tool.dg]` and
  imports the defs module by name (`importlib.import_module`), so it never
  reaches the `src/`-or-root path guess that stops `dg dev` and `dg check defs`
  (0022). Those two still fail with the folder in place.
- **The graph it builds is the graph the lists built.** With `assets.py` split
  into one module per layer, the executable asset keys, every asset check, the
  keys each of the four jobs materializes, the resources and the executor all
  matched the hand-listed build at `76b9f11`, and the webserver's code server
  reported the location loaded.
- **A definition imported by name into a second module is registered twice.** A
  sibling module importing three assets from another raised the registered
  asset definitions from nine to twelve. The graph collapsed them, because they
  were the same objects, so it is a listing that lies rather than an error.
- **`dg.load_assets_from_modules`** (and its checks counterpart) loaded the same
  set from the single module, undeprecated. It names modules instead of assets.
- **A per-folder `defs.yaml` works here too**: a `post_processing` block tagged
  and set owners on exactly the three `analytics/*` assets.
- **The integration components cannot express this graph.**
  `DltLoadCollectionComponent` names every op `dlt_assets_<source>_<dataset>`,
  and all three dlt blocks here share `public_indicators` and `raw`: rebuilding
  its `build_defs` failed with `Detected conflicting node definitions with the
  same name`. The body it generates takes no run config, so `YearRange` cannot
  reach it, and the backfill recipes address their config to the op name
  `ingest_by_year`. `DbtProjectComponent` keeps its own copy of the manifest as
  component state, refreshed by `prepare_if_dev` (on by default, the call 0022
  removed) or `dg utils refresh-defs-state`, beside the parse `just dbt-parse`,
  the Dockerfile and CI already run.

## Decision

- Every asset and asset check is a module-level definition in
  `orchestration/defs/`: `ingest.py` (dlt, and the landing zone's snapshot
  expiry), `models.py` (dbt), `analytics.py` (Polars), `site.py` (Evidence) and
  `checks.py`. `definitions.py` merges `dg.load_from_defs_folder` with what has
  no layer of its own: the four jobs, the schedule, the resources and the
  executor, still written out.
- What is not a definition stays outside the folder: the freshness policies in
  `orchestration/freshness.py`, the resources in `orchestration/resources.py`.
- A module needing another layer's definition imports the module
  (`from orchestration.defs import analytics`), never the definition. Keys and
  constants import by name.
- The checks stay in one module, `checks.py`.
- The registration tests are deleted: there is no list for an asset to be
  missing from.

## Rejected

- **The list, held by a test.** It worked, but it is the failure the framework
  now makes impossible, guarded after the fact. The two traps the test
  documented (`get_all_asset_keys()` counting unregistered dependencies as
  present; `AssetChecksDefinition` subclassing `AssetsDefinition`, so an
  `isinstance` chain in the wrong order measured nothing) existed only to
  compare a list with a graph. The first still governs `_job_keys` in
  `tests/test_definitions.py`, which says so.
- **`load_assets_from_modules` over the one module.** The same guarantee for
  one file, with nothing moved. Split by layer, it needs every module named, so
  the hand-maintained list survives one level up, and a forgotten module is the
  old omission again.
- **One check beside each asset**, the usual layout. `tests/test_asset_checks.py`
  points the checks at throwaway files by patching their module's globals
  (`DUCKDB_PATH`, `LAKEHOUSE_DIR` and four more). Spread over four modules, a
  missed patch, or a global another module imported by name, leaves a check
  reading the real warehouse while its test passes.
- **The jobs and schedule in `defs/` as well.** They would autoload too, but a
  job selects across every layer, and `definitions.py`'s docstring is where why
  there are four of them is told.
- **The integration components** (`DltLoadCollectionComponent`,
  `DbtProjectComponent`, the function and script components): the op-name
  collision and the missing run config above, a second manifest pipeline, and a
  translator that is a Python subclass either way. Overriding `build_defs` to
  get around the first is the decorator again inside a class. The function and
  script components would wrap six-line `@dg.asset` functions in YAML pointing
  back at them.
- **Moving `src/` to make `dg dev` work** stays rejected for 0022's reasons; the
  folder does not need it.

## Consequences

- **An asset defined outside `orchestration/defs/` is silently absent**, the old
  failure narrowed to one rule a reader can see. `AGENTS.md` states it.
- **A definition imported by name into another `defs/` module registers twice**,
  which the graph tolerates while it is the same object. `defs/__init__.py`
  states the rule.
- **Anything at module level in `defs/` that is a definition joins the graph**,
  including one half-written. `full_refresh` and `publish_site_from_raw` select
  by exclusion already, so a new asset joining unannounced is how they work.
- **Paths moved**: `orchestration/assets.py` is cited by name in the docs, the
  skills and the code comments, and moves with them; `tests/test_course.py`
  catches a stale path in a skill, but nothing checks `AGENTS.md`'s.
- **`dg dev` and `dg check defs` are still out of reach**, and `just dagster` still
  runs `dagster dev` (0022). Splitting the module did not change that.

**Revisit when** a layer outgrows its module (a folder per layer, with a
`defs.yaml` if one needs shared tags or owners), or when the integration
components accept several loads of one source and run config, which is what
would make the dlt half expressible in YAML.
