---
name: pipeline-observability
description: The pipeline's self-report — transform/pipeline_status.py and the four analytics.pipeline_* tables (sources, tables, tests, runs) that reports/pages/pipeline.md renders. How a dbt test's verdict is read from its fail_calc, why audit-table names come from the manifest, why stale audit tables are dropped, and why pipeline_runs is an appended, carried history that must find dbt's run_results.json. Use when editing transform/pipeline_status.py, the pipeline page, the run_history_records_this_build check, or when pipeline_runs or pipeline_tests look empty or wrong.
---

# Pipeline observability (`transform/pipeline_status.py`)

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
  both as failing against a build that finished ERROR=0. All but two tests use
  the default. `severity: warn` carries across as `status='warn'`.
- **An audit table the manifest does not name is stale, and dropped.** dbt never
  removes one, and renaming a model orphans all its tests' tables, which are empty
  and would score as passing. The filter applies only when a manifest is present.
- **It excludes its own output from the inventory**, and must run after
  `dbt build`, which writes the audit schema and the manifest it reads.

## `pipeline_runs` is a history

It is appended (`db.append_frame`), never replaced: `run_results.json` holds only
the latest invocation. The insert is idempotent on `invocation_id`, and a `Carry`
rule in `CARRIED` (`publish/restore_history.py`) carries it between releases —
the first rule to name its tables, because the rest of `analytics` is rebuilt
every run. That makes it the third table no rebuild can reproduce, beside the two
snapshots in `history`.

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
- **A fixture run must not write here.** `just test-pipeline` points dbt's
  artifact paths (`DBT_TARGET_PATH`, `DBT_MANIFEST_PATH`,
  `DBT_RUN_RESULTS_PATH`, `--target-path`) at its throwaway tree, or the next
  `just pipeline-status` files the fixture's timings in the real build history.
  `tests/test_workflows.py` holds all four, because each is invisible when
  missing: the fixture run passes and the next command is the one that is wrong.
