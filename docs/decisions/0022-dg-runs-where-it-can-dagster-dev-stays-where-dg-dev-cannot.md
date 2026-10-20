# 0022. `dg` runs where it can; `dagster dev` stays where `dg dev` cannot

Status: accepted 2026-10-19 (#155)

## Context

Every Dagster command the recipes ran carried `@superseded` with a runtime
warning: `dagster dev`, `dagster job execute`, `dagster asset materialize` and
`dagster definitions validate`, each naming a `dg` command instead. CI printed
the warning on every build. A supersession has no removal date (none of the four
carries a `breaking_version`), so this was never a deadline; the question was
whether the repo shows current Dagster practice.

A spike answered it on a throwaway clone with fixture data, on dagster 1.13.26,
the latest release:

- **`dg` refuses to run without `[tool.dg]` in `pyproject.toml`.** Three lines
  suffice (`directory_type = "project"`, `root_module = "orchestration"`, the
  location name); the default target is `orchestration.definitions`. The
  `dagster` CLI falls back to the same block when it finds no `[tool.dagster]`,
  so one block names the location for both.
- **`dg dev` and `dg check defs` cannot run on this layout.** Both build a
  workspace file from `[tool.dg]`, and `DgContext.get_path_for_local_module`
  looks for the root module under `src/` whenever `src/` exists, else at the
  root, never both. `src/` holds only the domain-neutral `modern_data_stack`,
  beside the top-level `orchestration/`, so both exit with `Cannot find module
  'orchestration' in the current project`. Neither takes `-m`. dagster's main
  branch has the same lookup.
- **Upstream once looked in both places.**
  [dagster#28127](https://github.com/dagster-io/dagster/pull/28127) made the
  function a direct module-to-path conversion from the project root;
  [dagster#29006](https://github.com/dagster-io/dagster/pull/29006) made it
  "look for packages in both `src` and in root", for `src`-layout projects; and
  [dagster#30432](https://github.com/dagster-io/dagster/pull/30432), a registry
  refactor, narrowed it to `src/` whenever `src/` exists. The comment above the
  line still says it looks in both. No issue tracks the difference.
- **`dg launch` and `dg list defs` do run.** They resolve the target as a module
  and never reach that lookup.
- **`dg launch` executes in process, through `job_execute_command_impl` and
  `asset_materialize_command_impl`**, the code behind the commands it replaces.
  A launched run went straight to SUCCESS past a `QueuedRunCoordinator`, as the
  old recipes did, so the one-writer behaviour is unchanged. Its source carries
  a TODO to use the instance's run launcher instead.
- **The `dg` group sets `DAGSTER_IS_DEV_CLI` for every subcommand**, the variable
  `DbtProject.prepare_if_dev()` reads. With that call in
  `orchestration/resources.py`, `dg launch --job load_retail` took 15.8 s
  against 5.0 s for `dagster job execute`, and 4.9 s without the call:
  `dbt deps` and `dbt parse` on every load.
- **`dg dev` is `dagster dev` with a generated workspace file.** It writes the
  file and calls `dagster dev`'s `dev_command_impl`, which creates the instance,
  starts one code server and points the webserver and the daemon at it. Running
  the two processes directly, as `just serve` does, differs on both counts:
  each starts its own code server, so the graph is imported twice and the
  daemon's is replaced every 60 s, and on an empty `DAGSTER_HOME` both create
  the storage tables at once. In ten paired instance initialisations on an empty
  directory, 12 of the 20 processes died on `table … already exists`, and none
  once the instance had been created first. Each code server also builds the
  dlt pipeline on import: on an empty dlt state directory the two collided on
  `FileExistsError` in 3 of 10 cold starts of the compose stack under load, and
  none once the definitions had been imported first.
- **`dg list defs` fails where `dagster definitions validate` fails to load**:
  exit 1, error on stderr, for an import error and for an asset requiring a
  resource nobody provides. It stops there. `definitions validate` then calls
  `validate_partitions()` on the asset graph and `dg list defs` does not:
  measured by mutation, an unpartitioned asset reading the monthly retail asset
  through a `StaticPartitionMapping` exits 0 under `dg list defs` and 1 under
  `definitions validate`. (Two partitions definitions in one job fail both,
  because resolving the job fails the load.) And it loads inside
  `disable_dagster_warnings()`, so a deprecated Dagster API in `orchestration/`
  prints nothing there. Nor does it build the repository snapshot the webserver
  serves, which `definitions validate` did, but no case is known where that
  matters: nine definitions written to load and fail the snapshot either failed
  the load or snapshotted and serialised cleanly.
- **`dagster-dg-cli` adds 16 packages to a tree of 164**, removing none, at the
  locked version of `dagster`.

## Decision

- `just materialize`, `materialize-site`, `materialize-site-from-raw`,
  `materialize-select` and both `backfill-*` recipes run `dg launch`.
- `just validate` runs `dg list defs`, discarding the listing, then calls
  `validate_partitions()` on the graph, the check `dg list defs` leaves out;
  `tests/test_definitions.py` calls it too.
- `just dagster` stays on `dagster dev`, the code `dg dev` would run, and prints
  its `SupersessionWarning`. It depends on `dbt-parse`.
- `orchestration/resources.py` has no `prepare_if_dev()`.
- `dagster-dg-cli` joins the `orchestration` group, and `[tool.dg]` replaces
  `[tool.dagster]` in `pyproject.toml`, so the location's name is written once.
- `dagster asset list` (`materialize-preview`) and `dagster job launch` (the
  compose recipe) stay: neither is superseded, and `job launch` submits to the
  queue, which `dg launch` does not.

Only `just dagster` prints a `SupersessionWarning`, and nothing in CI runs it.

## Rejected

- **Making `dg dev` and `dg check defs` run through configuration.**
  `root_module = "modern_data_stack"`, `defs_module = "modern_data_stack"`,
  `code_location_target_module = "orchestration.definitions"` and the repo root
  on `PYTHONPATH` makes both pass. It declares the domain-neutral package to be
  the Dagster project, which it is not, so `dg scaffold` would write project code
  into `src/`; it needs `PYTHONPATH` in the justfile, CI's setup action and the
  Dockerfile; and it rests on lookup behaviour nothing documents.
- **Moving the package out of `src/`.** A flat layout with uv_build's
  `module-root = ""` makes `dg dev` and `dg check defs` work with honest
  configuration, measured. It changes the package layout to satisfy one
  heuristic in one tool: five pytest cases, about sixteen tracked files that cite
  `src/`, the Dockerfile's layer order and the template repository all move with
  it.
- **Asking upstream to try both places.** The cleanest fix, and outside this repo.
  It is the trigger below rather than a dependency.
- **`dagster-webserver` and `dagster-daemon run` for `just dagster`, as
  `just serve` runs them.** No warning, but it gives up the one shared code
  server and the instance created before either process starts, and so brought
  in the races above. `just dagster` runs only on a laptop, where the warning is
  one line in a terminal; `just serve` has reasons of its own to run the pair
  ([0005](0005-just-serve-first-container-second.md)) and now creates the
  instance and imports the definitions first.
- **Staying on the superseded commands everywhere.** Supported, with no removal
  date, but every CI build would keep warning about a gap that measured
  replacements close.
- **Keeping `prepare_if_dev()`.** It bought one convenience, a model edited while
  the UI runs reaching the graph on a reload, and under `dg` it costs a dbt parse
  on every launch, in CI included.
- **`dg launch` for the compose recipe.** It would execute inside the
  `docker compose exec` shell instead of submitting a run to the containerised
  instance's queue and launcher.

## Consequences

- **`just dagster` prints a `SupersessionWarning` naming `dg dev`**, which a
  reader cannot follow here. The justfile comment and the
  `dagster-graph-and-jobs` skill point here.
- **A model edited while `just dagster` runs reaches the graph after
  `just dbt-parse` and a reload of the code location**, not on the reload alone.
- **`just validate` takes two Python start-ups instead of one**, the second for
  the partition mappings, and a Dagster deprecation raised by `orchestration/`
  no longer prints in CI's validate step.
- **`just dagster` no longer starts offline**, like every other Dagster recipe.
  It depends on `dbt-parse`, whose `dbt deps` reads hub.getdbt.com's index on
  every run, installed packages or not, so with no network it exits 2 before
  the UI. `prepare_if_dev()` ran the same commands inside the code location,
  where a failure left the UI up showing the location's error. A build holding
  the warehouse does not stop it: `dbt parse` opens no connection.
- **`dg launch` needs `DAGSTER_HOME`.** Without it the run uses a throwaway
  instance, records nowhere and applies none of `dagster.yaml`. Every recipe
  exports it.
- **`dg`'s telemetry is off only while `DAGSTER_HOME` names the repo's
  instance**, whose `dagster.yaml` disables it; `pyproject.toml` cannot.
- **The image carries the sixteen packages**, because it installs the
  `orchestration` group.
- [0004](0004-agent-plugins-kept-by-measured-use.md) rejected keeping
  `dagster-expert` partly because the repo did not install `dg`. It does now; the
  plugin stays off on 0004's measured-use rule, and its successor is
  `dagster@dagster`.
- [0005](0005-just-serve-first-container-second.md),
  [0006](0006-runs-launch-from-the-service-image-id.md),
  [0007](0007-workflows-run-through-just.md) and
  [0011](0011-deploy-is-the-containers-instance.md) name the commands that ran
  when they were written; their decisions stand.

**Revisit when** a Dagster release changes `get_path_for_local_module` to look in
both places again, as #29006 had it (then `dg dev` replaces `dagster dev` and
`dg check defs` replaces `dg list defs`, if it validates partitions), or when
`dg launch` starts launching through the instance (then the recipes queue
behind the service, and the one-writer edges in `dagster-graph-and-jobs` need
measuring again).
