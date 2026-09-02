---
name: dagster-graph-and-jobs
description: This repo's Dagster graph — the two partitioned assets and the guards a partitioned asset needs to keep working unpartitioned, why there are three jobs and why load_retail runs first, the hand-maintained lists in definitions.py and the two traps in the test that guards them, and the costed decision not to be a dg-shaped project or to use declarative automation. Use when adding or registering an asset or asset check, changing a job or selection, running a backfill, or before reaching for dg, components or AutomationCondition.
---

# The Dagster graph (`orchestration/`)

Dagster wraps the existing layers; it doesn't replace them. The facts that must
not depend on this skill loading — asset keys as the join between layers, the
`from __future__ import annotations` ban, and the single-process executor — are
in `CLAUDE.md`'s *Orchestration* section. This file is the rest.

The vendor `dagster-expert` skill overlapped this barely at all and **is no
longer enabled** (2026-09-02, zero invocations across 211 transcripts covering 9
commits to `orchestration/`). The last bullet below is why: it is written around
a `dg` CLI this project does not install. So this file is the Dagster knowledge
for this repo, not a supplement to a vendor one.

## Partitions

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

## Not a `dg`-shaped project

- **This is deliberately not a `dg`-shaped project, and the two halves of that
  decision are separable.** `create-dagster` scaffolds a `defs/` tree that
  autoloads, a `[tool.dg.project]` block and YAML components; `dagster-expert`,
  the vendor skill, was written around the `dg` CLI and assumed all of it —
  which is what eventually retired it from `.claude/settings.json`, a plugin
  arguing for a different project. Costed 2026-08-25 rather than assumed:
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
