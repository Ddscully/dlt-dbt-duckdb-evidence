---
name: dagster-graph-and-jobs
description: This repo's Dagster graph — why WDI and weather take their backfill years as run config rather than partitions (a partitioned job's Materialize button is a backfill), the one partitioned asset and the guards it needs to keep working unpartitioned, why there are four jobs and why load_retail runs first, the hand-maintained lists in definitions.py and the two traps in the test that guards them, which dg commands run here and why dg dev and dg check defs cannot, and the costed decisions against components and declarative automation. Use when adding or registering an asset or asset check, changing a job or selection, running a backfill, or before reaching for dg, components or AutomationCondition.
---

# The Dagster graph (`orchestration/`)

Dagster wraps the existing layers; it doesn't replace them. The facts that must
not depend on this skill loading — asset keys as the join between layers, the
`from __future__ import annotations` ban, the single-process executor, the job
order and hand registration — stay as one-liners in `AGENTS.md`'s
*Orchestration* section. This file is the whole account.

## Standing rules of the graph

`ingest`, `dbt` and `transform` stay independently runnable, and
`orchestration/assets.py` imports them rather than duplicating logic
(`build_pipeline()`, `dbt build`, `transform.co2_intensity.run()`).

- **Asset keys are the join between the layers.** dlt resources are keyed
  `raw/<resource>` by `RawSchemaDltTranslator` to match the keys dagster-dbt
  derives from `_sources.yml`. Rename a dbt source table without renaming the dlt
  resource and the graph silently splits in two — both halves still run. Check
  with `just validate` and a look at the graph.
- **`orchestration/assets.py` must not use `from __future__ import annotations`.**
  Dagster inspects the `context` parameter's annotation *object*; a stringified
  one fails with a confusing "Cannot annotate `context`".
- **Everything runs in one process** (`in_process_executor`, and the five
  unwindowed resources — the four `replace` ones plus `ecb_fx_rates` — in a
  single op): DuckDB takes one writer at a time, so parallel steps would fight
  over the lock.
- **And one run at a time, which is instance config, not code.** The executor
  serialises steps *within* a run; two runs are two processes. `.dagster/dagster.yaml`
  sets `concurrency: runs: max_concurrent_runs: 1` (Dagster's default is 10), so
  a UI launch, a UI backfill or a schedule tick queues behind a run in progress.
  Its edges, measured on 1.13.22 unless a bullet names a later version:
  - **`dg launch` bypasses the queue, and every recipe that materialises runs
    it**, the `backfill-*` recipes included: it executes in process, through
    the code behind `dagster job execute` and `dagster asset materialize`
    (measured for `dg launch` on 1.13.26). The queue still *counts* such a
    run — a UI launch waits for a `just materialize` to end — but no recipe
    waits for anything.
  - **The file is read at process start.** Editing it changes nothing until
    `just serve` or `just dagster` restarts; `dagster instance info` prints what
    the instance loaded.
  - **A `DAGSTER_HOME` without it falls back to ten**, with one startup notice. A
    symlink to the checked-in file works
    ([`docs/RUNNING_AS_A_SERVICE.md`](../../../docs/RUNNING_AS_A_SERVICE.md#invariants-that-fail-silently-in-a-service)).
  - **There are two instance configs, and they must agree.** `deploy/dagster.yaml`
    is the deployed one (`DAGSTER_HOME=<repo>/deploy`), differing from
    `.dagster/dagster.yaml` in putting run, event and schedule storage in
    Postgres and in launching each run in its own container. Dagster has no
    include, so the limit above is written twice, and
    `tests/test_dagster_instance.py` holds the copies in step — a laptop
    measurement is only evidence about a deployment while it does.
  - **`deploy/` is the container's instance, and a host must not point at it.**
    Nothing in Dagster checks whether it is running in a container, so a host
    process resolves the launcher and starts cleanly, then fails when the daemon
    dequeues a run (1.13.23): `no container '<hostname>' on this Docker
    daemon`. Behind that, eleven of the launcher's eighteen copied variables are
    unset on such a host, and no host configuration satisfies the list.
    In-process recipes (`just materialize*`, `backfill-*`) work under either
    instance, so the break shows only on a UI click, a schedule tick or a
    backfill
    ([`docs/decisions/0011-deploy-is-the-containers-instance.md`](../../../docs/decisions/0011-deploy-is-the-containers-instance.md)).
  - **The limit survives the container boundary, measured on 1.13.23.** Two
    runs launched ten seconds apart against the compose stack gave one run
    container and one `QUEUED` row; the second started only when the first
    finished, and `auto_remove` left no exited containers. So `DockerRunLauncher`
    changes where a run executes and not how many execute.
- **The Evidence site is an asset, excluded from `full_refresh` because it needs
  Node.** The `evidence_site` asset shells out to npm; `ci.yml`, `live.yml` and
  `release-data.yml` run `full_refresh` with no Node, and `pages.yml` runs
  `publish_site`, or `publish_site_from_raw` over the landing zone `live.yml`
  cached. Every selection names what it leaves out, so a second npm-shaped or
  partitioned asset has to be excluded by hand too.
  How the site meets the graph is `building-evidence-reports`.
- **Importing `orchestration.assets` leaves a dlt pipeline active process-wide.**
  The `@dlt_assets` decorators call `build_pipeline()` at import time, so a later
  test calling a resource directly reads the real `~/.dlt` state and fails on
  pagination it never got wrong — only in a full-suite run, only on a machine
  that has loaded WDI. `tests/conftest.py` deactivates it on teardown.
- The `daily_refresh` schedule ships `STOPPED`, so opening the UI does not start
  hammering public APIs. It targets `full_refresh`, so it never builds the site.
  **Once started, a daemon that comes up after a missed 06:00 UTC tick launches
  it within seconds** — only the latest, because a schedule with no partition set
  does not catch up — which is why a click on Materialize just after
  `just serve` starts is the case the one-run queue exists for.
- Dagster state lives in `.dagster/` (`DAGSTER_HOME`, exported by the justfile);
  only `dagster.yaml` is checked in. Under `DAGSTER_HOME=<repo>/deploy` it lives
  in Postgres instead, and `.dagster/` is never written at all.
- **A run container's command is `dagster api execute_run`, not a recipe.**
  `DockerRunLauncher` does not go through `just`, so anything the image only
  exposes via `uv run` is not on a run container's `PATH`. The Dockerfile puts
  `/app/.venv/bin` there for exactly this; without it a run fails with
  `exec: "dagster": executable file not found in $PATH` and `auto_remove` deletes
  the evidence (measured).
- **Against a running service, never pass `-m` to the CLI.** A schedule's
  identity includes the code location *name*, which `-m orchestration.definitions`
  sets to the module while `[tool.dg.project]` sets it to `modern_data_stack`, so
  `dagster schedule start -m …` prints success and flips a row the daemon does
  not read, and `dagster job launch -m …` returns 0 and then fails the run with
  `DagsterCodeLocationNotFoundError`. Both measured
  ([`docs/RUNNING_AS_A_SERVICE.md`](../../../docs/RUNNING_AS_A_SERVICE.md#invariants-that-fail-silently-in-a-service)).
- **Some commands *require* `-m`, and the two rules do not collide.** The split
  is which click options a command carries. `dagster asset list` takes
  `python_pointer_options` alone (`-m`, `-f`, `--package-name`), so a bare
  `dagster asset list` exits with `Error: Invalid set of CLI arguments for
  loading repository/job`; `dagster job list` and `dagster definitions validate`
  take `workspace_options`, which do fall back to `pyproject.toml`. Measured:

  | Command | `-m` | `DAGSTER_HOME` |
  |---|---|---|
  | `dagster asset list` (`just materialize-preview`) | **required** | not read |
  | `dagster job list` | optional — omit it | not read |
  | `dagster definitions validate` | optional — omit it | a temp dir if unset |
  | `dg list defs` (`just validate`) | not needed: it reads `[tool.dg]` | not needed |
  | `dg launch` (`just materialize*`, `backfill-*`) | not needed: it reads `[tool.dg]` | **a throwaway instance if unset**: exit 0, the run recorded nowhere, and none of `dagster.yaml` applied — the loggers and the telemetry opt-out included |
  | `dagster run list`, `dagster schedule list` | n/a | **required** |

  What requires `-m` builds the definitions in a throwaway code server and
  touches no instance state, which is why naming that location after the module
  costs nothing there. The rule above is about the commands that *write*
  instance state — `schedule start`/`stop`, `sensor start`/`stop`, `job launch`,
  `run delete`, `asset wipe`. An instance command with no `DAGSTER_HOME` ends its
  traceback with `export DAGSTER_HOME=…`, and `definitions validate` without one
  makes a `.tmp_dagster_home_*` (gitignored) that it removes on exit; every
  `just` recipe has it exported already.

**This file is the Dagster knowledge for this repo**, not a supplement to a
vendor skill. Dagster's skill (the `dagster@dagster` plugin, which replaced
`dagster-expert`) is not enabled: it was retired on measured use
([`docs/decisions/0004-agent-plugins-kept-by-measured-use.md`](../../../docs/decisions/0004-agent-plugins-kept-by-measured-use.md)),
and it assumes a project `dg dev`, `dg check defs` and scaffolding can run in,
which this one is not (`dg` below).

## Backfill windows, and the one partition

- **`raw/wb_wdi` and `raw/om_weather_daily` take a year range as run config, not
  partitions, and the Materialize button is why**: a job takes its assets'
  partitions definition, and a partitioned job's Materialize button is a
  backfill of every year since 1960. The measurements, and what was weighed, are
  [`docs/decisions/0002-yearly-sources-as-run-config.md`](../../../docs/decisions/0002-yearly-sources-as-run-config.md).
  - **`YearRange` is the config, on the `ingest_by_year` op.** Unset loads the
    lookback; `first_year` (with `last_year`, which defaults to it) loads exactly
    that closed range, bounded where the partitions sat: WDI's 1960 and the
    current year, past which the World Bank serves projections. Validated in the
    body, before any load. From the UI, a backfill is the Launchpad with
    `ops: {ingest_by_year: {config: {first_year: 1990, last_year: 1995}}}`.
  - **`dg launch --assets` silently ignores config for an op it does not
    know**, because it runs `dagster asset materialize`'s code, where that was
    measured: a stale op name in `--config-json` loads the lookback and exits 0,
    where `dagster job execute` (`dg launch --job`) refuses the same config; a
    misspelt *field* is refused by both. The justfile spells the op, so
    `tests/test_definitions.py` holds both backfill recipes to
    `raw_by_year_assets.op.name`.
  - **A backfill deliberately doesn't move the WDI watermark.** The watermark
    means "everything up to here is loaded", which a run over one window can't
    claim: a 2020–2025 backfill into an empty warehouse would otherwise leave a
    2025 watermark and the next incremental run would look back five years over
    sixty years that were never fetched.
  - **One range is one run, whichever source.** WDI asks `&date=lo:hi`, so
    1990–2025 is 11 requests, one per indicator, not 396. Weather
    chunks by year inside the resource either way, to pace its budget, and
    refuses a range an hour's allowance cannot hold with a retry to spare
    before any request (`weather-models`).
  - **`tests/test_definitions.py` asserts `full_refresh` and `publish_site` have
    no `partitions_def`**, because one partitioned asset joining either
    selection brings the picker back with nothing else red.
- **Merging is not what earns a window, and `ecb_fx_rates` is the near-miss that
  proves it.** It is incremental *and* its API takes a date range — but its
  entire 27-year series is one three-second request, so it takes no window at
  all. The blocks split on `YEAR_RANGE_RESOURCES` and `PARTITIONED_RESOURCES`
  (in `ingest/pipeline.py`), not on the disposition, and `UNWINDOWED_RESOURCES`
  in `orchestration/assets.py` is derived as everything else. **Once there were
  two tuples and the WDI block was built from `INCREMENTAL_RESOURCES`
  directly** — adding a second merge resource to that constant would silently
  have given it yearly partitions. `load_groups` still owns the refresh/merge
  split; the blocks only decide who gets a window. `tests/test_ingest.py` holds
  the tuples to the source.
- **`raw/retail_invoice_lines` is the one partitioned asset, and stays one
  because every partition together is routine-sized.** A month is real work
  (converting it), the cached download makes every partition one fetch, and
  `invoice_month` comes from the partition key's timestamp, so re-running a
  month replaces exactly that month. Materializing all 25 months is one run over
  the one workbook — 17s — so its button's backfill costs what a
  routine load does. It still has to stay out of `full_refresh`, or that job
  becomes month-partitioned (below).
  - **The asset has two paths and the unpartitioned one has to keep working.**
    The justfile and all four workflows execute `load_retail` with no partition
    key. A partitioned asset in an unpartitioned run doesn't fail at plan time —
    it fails *inside the body*, at the first touch of `context.partition_key`. So
    the fallback is an explicit guard in the asset, not something the job gives
    you: no partition means the whole workbook.
  - **Guard on `has_partition_key` *and* `has_partition_key_range`.**
    `has_partition_key_range` is False for a run targeting a single partition, so
    testing it alone falls through to the unpartitioned branch — and it
    *succeeds*, having loaded the wrong window. Verified by doing it with
    `--partition 1995` while WDI was partitioned; `--partition 2010-03` would load
    the whole workbook the same way. `context.partition_key_range` itself covers
    both cases; it returns `start == end` for one key.
  - **`end` is exclusive, and the retail partitions were short a month because of
    it.** `TimeWindowPartitionsDefinition(start=RETAIL_FIRST_MONTH,
    end=RETAIL_LAST_MONTH)` reads like a closed interval and is not one: it
    resolved to 24 keys ending at `2011-11`, so December 2011's 25,526 lines had
    no partition to land in and no key that could ask for them. Nothing was ever
    red — every workflow and justfile recipe uses the *unpartitioned* path, which
    loads the whole workbook — so the only symptom was a per-partition backfill
    quietly stopping a month early. `_month_after(RETAIL_LAST_MONTH)` is the
    fix, keeping the constant meaning the data's last month, and
    `tests/test_definitions.py` now pins both ends and the key count. The yearly
    partitions needed `end_offset=1` for the open-ended half of the same
    off-by-one: a year's window only closes on 1 January, so without it the
    current year was not a partition.
  - `BackfillPolicy.single_run()` makes a month range one run, not one per
    month. It also means the CLI's `--partition-range` refuses any selection that
    reaches the *unpartitioned* downstream models, so a retail backfill targets
    the raw asset alone and you rebuild after it.

## Registration, and the four jobs

- **Every asset and check is listed by hand in `definitions.py`, and nothing
  tells you when one isn't.** `dg.Definitions` takes explicit lists, so an
  omission is not an error — the asset is simply not in the graph,
  `AssetSelection.all()` never sees it, and `just validate`
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
- **A job takes its assets' partitions definition, which is why `load_retail` is
  a job of its own.** `define_asset_job` resolves a selection to a single `partitions_def`,
  and raises on two — `allow_different_partitions_defs` is hardcoded `False` for
  named asset jobs and `True` only for Dagster's own implicit global job. With
  `raw/retail_invoice_lines` inside it, `full_refresh` would be month-partitioned
  (built and checked: `TimeWindowPartitionsDefinition`) and its Materialize
  button a backfill. So `load_retail` carries the retail ingest alone, `full_refresh` is
  `AssetSelection.all() - site - retail_ingest`, and **`load_retail` has to run
  first** because dbt reads the table it lands. The justfile recipes and all four
  workflows pair them; running `full_refresh` by itself against a fresh warehouse
  reproduces the catalog error above.
  - `dg launch --assets '*'` is not a way round it: it runs `dagster asset
    materialize`'s code, which refuses the partitioned retail asset without
    `--partition` ("Asset has partitions, but no '--partition' option was
    provided"), so the unpartitioned whole-graph run only exists as a job.
  - **A job shares a namespace with the ops**, so the job is `load_retail` and
    not `ingest_retail` — `@dlt_assets(name="ingest_retail")` already holds that
    name, and the collision reports as `Conflicting definitions found in
    repository with name 'ingest_retail'` naming `__ASSET_JOB`.
- **`publish_site_from_raw` is `publish_site` minus
  `AssetSelection.key_prefixes("raw", "lake")`**, the job `pages.yml` runs over a
  cached landing zone
  ([`docs/decisions/0020-the-site-builds-from-the-cached-landing-zone.md`](../../../docs/decisions/0020-the-site-builds-from-the-cached-landing-zone.md)).
  It needs no `load_retail` first, because it lands nothing.
  - **By prefix, not by block**: a new `@dlt_assets` block lands under `raw/` and
    stays out without an edit. A selection that names the blocks and misses
    one is the mutation the test was checked against, and it goes red.
  - **Subtracting assets subtracts their checks**, and that is the intent. The
    checks on `raw/` and `lake/` judge what an ingest landed —
    `wdi_indicators_all_present` reads `lakehouse.raw.wb_wdi` through a copy of
    `stg_wdi`'s filter, not the view — and a cache is saved only after a green
    materialize that ran them, so they have already judged the landing zone
    this job reads.
    `tests/test_definitions.py` asserts no check on `raw/` or `lake/` is in it.

## `dg`: where it runs, and `dagster dev` where it cannot

- **`dg launch` and `dg list defs` run here; `dg dev` and `dg check defs` cannot,
  and the reason is the layout, not taste**
  ([`docs/decisions/0022-dg-runs-where-it-can-dagster-dev-stays-where-dg-dev-cannot.md`](../../../docs/decisions/0022-dg-runs-where-it-can-dagster-dev-stays-where-dg-dev-cannot.md)).
  Every `dg` command needs `[tool.dg]` in `pyproject.toml`; without it, it exits
  with "must be run inside a Dagster project directory". `dg dev` and `dg check
  defs` then build a workspace file from that block, and
  `DgContext.get_path_for_local_module` looks for the root module under
  `<root>/src/` **whenever `src/` exists**, else at the root, never both. `src/`
  holds only `modern_data_stack`, so both fail with `Cannot find module
  'orchestration' in the current project`, and neither takes `-m`. `dg launch`
  and `dg list defs` resolve the target as a module and never reach the lookup.
  **Check `get_path_for_local_module` on each Dagster bump**: a lookup that tries
  both places makes the pair usable here.
  - **Pointing `dg` at the package under `src/` is not a way round it.**
    `root_module = "modern_data_stack"` wants a `defs/` folder inside the
    domain-neutral package, and with one the code location's working directory
    becomes `src/`, where `orchestration` does not import. It loads only with the
    repo root on `PYTHONPATH` as well, which the decision record rejects.
- **Each superseded command but `dagster dev` has a replacement here, and only
  `just dagster` prints a `SupersessionWarning`.** Four commands carry
  `@superseded` with `emit_runtime_warning=True` in the installed
  `dagster/_cli/`:

  | Superseded | Runs here instead | Recipe |
  |---|---|---|
  | `dagster dev` | itself: `dg dev` writes a workspace file and runs `dagster dev`'s code, and cannot write one here | `just dagster` |
  | `dagster job execute` | `dg launch --job` | `just materialize`, `materialize-site`, `materialize-site-from-raw` |
  | `dagster asset materialize` | `dg launch --assets` | `just materialize-select`, `backfill-wdi`, `backfill-weather` |
  | `dagster definitions validate` | `dg list defs`, which exits 1 on an import or a definition error, and `validate_partitions()` in `tests/test_definitions.py`, which `dg list defs` skips | `just validate`, `just test` |

  `dagster asset list` (`just materialize-preview`), `dagster job launch` (the
  compose recipe), `dagster-webserver`, `dagster-daemon` and `dagster api` are
  not superseded. **`dagster job launch` has to stay**: it submits to the queue,
  and `dg launch` does not.
- **`dagster dev` is not `just serve`'s pair under another name, so the pair is
  no way round its warning.** It creates the instance first and serves the
  webserver and the daemon from one code server. Run separately, each imports
  the graph into a code server of its own, and on an empty `DAGSTER_HOME` both
  create the storage tables at once: 12 of 20 processes died on `table …
  already exists` across ten paired starts. With no dlt state yet, both code
  servers also create dlt's pipeline folder at once, and the webserver's lost
  on `FileExistsError` in 3 of 10 loaded cold starts of the compose stack.
  `just serve` creates the instance and imports the definitions before starting
  either (`docs/RUNNING_AS_A_SERVICE.md`).
- **`dg launch` is `dagster job execute` under another name.** It calls
  `job_execute_command_impl` and `asset_materialize_command_impl` in process, so
  everything above about the queue, `-m` and ignored op config holds for it. Its
  own source carries a TODO to launch through the instance's run launcher
  instead; **when that lands, the recipes start queueing** behind the service and
  the one-writer edges at the top of this file change. Re-measure on a bump.
- **The `dg` group sets `DAGSTER_IS_DEV_CLI` for every subcommand**
  (`dagster_dg_cli/cli/__init__.py`), the variable
  `DbtProject.prepare_if_dev()` reads. With that call in `resources.py`, every
  `dg launch` and `dg list defs` runs `dbt deps` and `dbt parse` on load: 15.8 s
  against 4.9 s without it, for `load_retail` on fixtures. So there is no
  `prepare_if_dev()`, and `just dagster` depends on `dbt-parse` like the rest.
- **`dg`'s telemetry is off only while `DAGSTER_HOME` is set.** It needs both the
  user's own `dg` config and `$DAGSTER_HOME/dagster.yaml` to allow it; the repo's
  file says no and every recipe exports `DAGSTER_HOME`, but a bare `uv run dg …`
  outside `just` reports. `pyproject.toml` cannot turn it off: `dg` reads that
  setting from the user file alone.
- **The package is `dagster-dg-cli`, in the `orchestration` group: +16 packages
  on a 164-package tree**, removing none and pulling `dagster-cloud-cli`,
  `github3-py`, `httpx`, `questionary` and `yaspin` into a project with no
  Dagster Plus deployment, at the version of `dagster` already locked. The image
  installs the group, so it carries them too.
- **`uvx dg` is the trap, and this repo has already refused it twice.** It
  dodges the lockfile — which is the argument that lost pyright to ty
  ("an unpinned global binary no lockfile here can see") and the reason
  `ty-lsp` runs `uv run ty server` rather than a bare `ty`.
- **The `defs/` half is separable, and still not taken.** `create-dagster`
  scaffolds a `defs/` tree that autoloads; `dg.load_from_defs_folder` ships in
  `dagster` core, and a `defs/` folder merges with the hand-written
  `Definitions` (`dg.Definitions.merge`, measured). What it would buy is deleting
  `tests/test_definitions.py`, because an unregistered asset becomes impossible
  rather than merely caught. That trade is close to a wash: the test costs ~1s and
  *also* documents two traps that the framework would silently absorb
  (`get_all_asset_keys()` is too wide; `AssetChecksDefinition` subclasses
  `AssetsDefinition`, so an `isinstance` chain in the wrong order measures
  nothing).
- **Components would delete the explanation, which is the deliverable.** The
  vendor skill's dbt page reserves the pythonic `@dbt_assets` path for "complex
  customization" — `FolderGroupDbtTranslator` is exactly that, and its comment
  is longer than its code on purpose.
- **Declarative automation is the adjacent question, and the answer is no
  for reasons of shape, not of plumbing.** An `AutomationCondition` is
  evaluated by an automation sensor *in the daemon*, and the daemon runs only
  under `just dagster` and the service (`just serve`, compose); the four
  workflows are one-shot `dg launch` with none. Where it would fire, it would
  restate one legible eight-line `ScheduleDefinition`
  across nine asset definitions and be strictly *less* functional than the
  STOPPED schedule it replaced. Dagster's own decision tree routes "simple,
  fixed time-based execution" to schedules and reserves DA for partition-aware
  and graph-state-dependent triggering; `ScheduleDefinition` raises no
  deprecation warning on 1.13, so this is not a legacy path being tolerated.
  - **The partition angle is the near-miss.** One asset here *is* partitioned
    (the yearly sources take run config instead), which is DA's stated niche —
    but backfills are deliberately manual (`just backfill-wdi`, "an explicit
    act with a window you can point at"), so DA would automate precisely what
    this project chose to keep explicit.
  - **What would change it is circumstance, not taste**: cadences that
    diverge, on the service's daemon — FX is daily, OWID annual, retail a closed
    archive. Today everything moves together on one cron, so there is nothing
    for a condition to express. Note the repo already runs the *observability*
    half of that world: `FreshnessPolicy` on every asset. Heavy use of
    Dagster's modelling with almost none of its runtime is a coherent position
    here, not a half-finished adoption.
- **The vendor skill's depth and this repo's content are close to
  disjoint**, which is the part worth knowing before reaching for it. Grepping
  its 172 reference files for what `assets.py` actually calls:
  `FreshnessPolicy` 1 (in a components file, dead here), `BackfillPolicy` 0,
  `asset_check` 1 (in passing), against `AutomationCondition` 10 — which this
  project uses nowhere. It is worth loading for **asset selection syntax** and
  the **dagster-dbt/dlt integration pages**, and not for anything else here.
