---
name: dagster-graph-and-jobs
description: This repo's Dagster graph — why WDI and weather take their backfill years as run config rather than partitions (a partitioned job's Materialize button is a backfill), the one partitioned asset and the guards it needs to keep working unpartitioned, why there are three jobs and why load_retail runs first, the hand-maintained lists in definitions.py and the two traps in the test that guards them, and the costed decision not to be a dg-shaped project or to use declarative automation. Use when adding or registering an asset or asset check, changing a job or selection, running a backfill, or before reaching for dg, components or AutomationCondition.
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
  with `dagster definitions validate` and a look at the graph.
- **`orchestration/assets.py` must not use `from __future__ import annotations`.**
  Dagster inspects the `context` parameter's annotation *object*; a stringified
  one fails with a confusing "Cannot annotate `context`".
- **Everything runs in one process** (`in_process_executor`, and the `replace`
  resources in a single op): DuckDB takes one writer at a time, so parallel steps
  would fight over the lock.
- **And one run at a time, which is instance config, not code.** The executor
  serialises steps *within* a run; two runs are two processes. `.dagster/dagster.yaml`
  sets `concurrency: runs: max_concurrent_runs: 1` (Dagster's default is 10), so
  a UI launch, a UI backfill or a schedule tick queues behind a run in progress.
  Three edges, all measured on 1.13.22:
  - **`dagster job execute` and `dagster asset materialize` bypass the queue,
    and every recipe that materialises runs one of them**, the `backfill-*`
    recipes included. The queue still *counts* such a run — a UI launch waits for
    a `just materialize` to end — but no recipe waits for anything.
  - **The file is read at process start.** Editing it changes nothing until
    `just serve` or `just dagster` restarts; `dagster instance info` prints what
    the instance loaded.
  - **A `DAGSTER_HOME` without it falls back to ten**, with one startup notice. A
    symlink to the checked-in file works
    ([`docs/RUNNING_AS_A_SERVICE.md`](../../../docs/RUNNING_AS_A_SERVICE.md) §5, §8).
  - **There are two instance configs, and they must agree.** `deploy/dagster.yaml`
    is the deployed one (`DAGSTER_HOME=<repo>/deploy`), differing from
    `.dagster/dagster.yaml` in putting run, event and schedule storage in
    Postgres and in launching each run in its own container. Dagster has no
    include, so the limit above is written twice, and
    `tests/test_dagster_instance.py` holds the copies in step — a laptop
    measurement is only evidence about a deployment while it does.
  - **The limit survives the container boundary, measured.** Two runs launched
    ten seconds apart against the compose stack gave one run
    container and one `QUEUED` row; the second started only when the first
    finished, and `auto_remove` left no exited containers. So `DockerRunLauncher`
    changes where a run executes and not how many execute.
- **The Evidence site is an asset, excluded from `full_refresh` because it needs
  Node.** The `evidence_site` asset shells out to npm; `ci.yml`, `nightly.yml` and
  `release-data.yml` run `full_refresh` with no Node, and `pages.yml` runs
  `publish_site`. Both selections name what they leave out, so a second
  npm-shaped or partitioned asset has to be excluded by hand too.
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
  sets to the module while `[tool.dagster]` sets it to `modern_data_stack`, so
  `dagster schedule start -m …` prints success and flips a row the daemon does
  not read, and `dagster job launch -m …` returns 0 and then fails the run with
  `DagsterCodeLocationNotFoundError`. Both measured
  ([`docs/RUNNING_AS_A_SERVICE.md`](../../../docs/RUNNING_AS_A_SERVICE.md) §8).

**This file is the Dagster knowledge for this repo**, not a supplement to a
vendor skill: `dagster-expert` is not enabled, because it is written around a `dg`
CLI this project does not install
([`docs/decisions/0004-agent-plugins-kept-by-measured-use.md`](../../../docs/decisions/0004-agent-plugins-kept-by-measured-use.md)).

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
  - **`dagster asset materialize` silently ignores config for an op it does not
    know.** A stale op name in `--config-json` loads the lookback and exits 0,
    where `dagster job execute` refuses the same config; a misspelt *field* is
    refused by both. The justfile spells the op, so `tests/test_definitions.py`
    holds both backfill recipes to `raw_by_year_assets.op.name`.
  - **A backfill deliberately doesn't move the WDI watermark.** The watermark
    means "everything up to here is loaded", which a run over one window can't
    claim: a 2020–2025 backfill into an empty warehouse would otherwise leave a
    2025 watermark and the next incremental run would look back five years over
    sixty years that were never fetched.
  - **One range is one run, whichever source.** WDI asks `&date=lo:hi`, so
    1990–2025 is 11 requests, one per indicator, not 396. Weather
    chunks by year inside the resource either way, to pace its budget, and
    refuses a range over a day's allowance before any request (`weather-models`).
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

## Registration, and the three jobs

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
- **A job takes its assets' partitions definition, which is why there are three
  jobs.** `define_asset_job` resolves a selection to a single `partitions_def`,
  and raises on two — `allow_different_partitions_defs` is hardcoded `False` for
  named asset jobs and `True` only for Dagster's own implicit global job. With
  `raw/retail_invoice_lines` inside it, `full_refresh` would be month-partitioned
  (built and checked: `TimeWindowPartitionsDefinition`) and its Materialize
  button a backfill. So `load_retail` carries the retail ingest alone, `full_refresh` is
  `AssetSelection.all() - site - retail_ingest`, and **`load_retail` has to run
  first** because dbt reads the table it lands. The justfile recipes and all four
  workflows pair them; running `full_refresh` by itself against a fresh warehouse
  reproduces the catalog error above.
  - `dagster asset materialize --select '*'` is not a way round it: the CLI
    refuses the partitioned retail asset without `--partition` ("Asset has
    partitions, but no '--partition' option was provided"), so the unpartitioned
    whole-graph run only exists as a job.
  - **A job shares a namespace with the ops**, so the job is `load_retail` and
    not `ingest_retail` — `@dlt_assets(name="ingest_retail")` already holds that
    name, and the collision reports as `Conflicting definitions found in
    repository with name 'ingest_retail'` naming `__ASSET_JOB`.

## Not a `dg`-shaped project

- **This is deliberately not a `dg`-shaped project, and the two halves of that
  decision are separable.** `create-dagster` scaffolds a `defs/` tree that
  autoloads, a `[tool.dg.project]` block and YAML components; `dagster-expert`,
  the vendor skill, was written around the `dg` CLI and assumed all of it.
  Costed rather than assumed:
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
    - **The partition angle is the near-miss.** One asset here *is* partitioned
      (the yearly sources take run config instead), which is DA's stated niche — but backfills
      are deliberately manual (`just backfill-wdi`, "an explicit act with a
      window you can point at"), so DA would automate precisely what this
      project chose to keep explicit.
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
    components file, dead here), `BackfillPolicy` 0,
    `asset_check` 1 (in passing), against `AutomationCondition` 10 — which this
    project uses nowhere. It is worth loading for **asset selection syntax** and
    the **dagster-dbt/dlt integration pages**, and not for anything else here.
