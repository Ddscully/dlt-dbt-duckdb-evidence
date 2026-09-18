# Running this warehouse as a service

> **§2's `just serve` is built, and so is the Postgres-backed instance §3, §5
> and §10 describe; everything else here is still a design.** The recipe is in
> the justfile and was run rather than sketched — three processes, twelve once
> Dagster's code servers are counted, and a measured answer for what happens to
> all of them when one dies. `deploy/dagster.yaml` is likewise real: run, event
> and schedule storage in Postgres, run through a restart rather than argued
> about. What does not exist is everything around them: no unit file, no
> container, no swap asset (§4), and no host anybody has stood this up on. The pipeline still runs from `just` recipes on a laptop and
> from four GitHub workflows on cron, and that is the whole of what runs
> unattended today. Read §2 as instructions and the rest as a plan.

Today the pipeline has two homes and neither is a service. Locally it is
`just run` or `just materialize`, invoked by a person. In CI it is four
workflows on GitHub's cron. The `daily_refresh` schedule in
[`orchestration/definitions.py`](../orchestration/definitions.py) exists and
ships `STOPPED`, so nothing evaluates it.

"As a service" here means **one host, running continuously**: the asset graph
scheduling itself, the dashboard served without a deploy step, and the freshness
policies actually evaluated. Not multi-tenancy, not a query API, not a cluster.
[`docs/FOR_REVIEWERS.md`](./FOR_REVIEWERS.md#4-what-breaks-at-1000) covers where
this shape stops scaling, and none of that changes.

## 1. What it replaces

| Workflow | Under a service |
|----------|-----------------|
| `ci.yml` | **stays.** It is about the repo, not the data: fixture-backed, offline, per PR. A service has nothing to say about a pull request. |
| `nightly.yml` | **redundant**, if the service runs live daily and alerts. Its job is to distinguish "we broke it" from "OWID is down", and a service that ingests live inherits exactly that signal. |
| `pages.yml` | **redundant** if the service serves the site. Keep it only if the public mirror is wanted for its own sake. |
| `release-data.yml` | **stays, and the service deliberately does not do it.** GitHub is the distribution channel; the service is not. Publishing is a monthly, outward-facing act with its own obligations (attribution, pseudonymisation, a storage-format ceiling), and none of them get easier by moving to a host that is also serving traffic. See §4 for what the service borrows from it and what it leaves behind. |

**The gain is not parity, it is that the SLA starts being enforced.** The
freshness policies in [`orchestration/assets.py`](../orchestration/assets.py)
(warn at two days without a load for `raw/*`, fail at seven; the modelled layers
rebuilt by 08:00 UTC) are declared today and **evaluated by nothing** between CI
runs. A schedule that quietly stopped firing is supposed to show as a stale asset
rather than as an absence somebody notices; that only happens with a daemon
running. See [`docs/FOR_REVIEWERS.md`](./FOR_REVIEWERS.md#2-what-is-the-freshness-sla-and-what-happens-when-it-is-missed).

## 2. `just serve`, and the container built on it

**Recommendation: a `just serve` recipe, supervised by systemd. Reach for a
container only when the target demands one** (several hosts, immutable images).
Even then its `CMD` should be `just serve`, so it inherits the definition rather
than restating it.

Four reasons, all specific to this repo rather than to taste:

1. **The justfile is already the single definition of the environment.**
   [`.github/actions/setup`](../.github/actions/setup/action.yml) exists
   *because* four workflows each restated that environment and two restated it
   wrongly. A Dockerfile is a fifth restatement of the same facts (a base
   image, an OS package set, a Node install, an env block), and
   `tests/test_workflows.py`, which is what stops the other four drifting,
   cannot guard it, because it is not a workflow.
2. **A base image is a new pinning surface nothing watches.** Three versions
   here can only age deliberately (`.python-version`, the sqlfluff pair, ruff)
   because no Dependabot ecosystem covers them. A base image tag would be a
   fourth thing somebody has to remember, for no functional gain on one host.
3. **A container does not solve the constraint that actually binds.** The
   single-writer lock is a property of *the file plus a process*, not of the
   host. Two containers sharing a volume reintroduce it across a filesystem
   boundary, strictly worse than one process tree, where `in_process_executor`
   serialises a run's steps and the instance's one-run queue serialises the runs
   (§5).
4. **The serving half needs no runtime at all.** `evidence sources` extracts the
   warehouse tables to Parquet under `reports/.evidence/`, `evidence build`
   renders static HTML into `reports/build/`, and the browser queries that
   Parquet with DuckDB-WASM. **The served site never opens
   `data/warehouse.duckdb`.** Serving it is a static file server, and there is
   nothing there to containerise.

### What changed, 2026-09-17

The four reasons above are kept as written, because the recommendation has not
been reversed — **the laptop default is still the file and still `just`**. What
changed is that "reach for a container when the target demands one" now has a
built answer rather than a sketch: `Dockerfile`, `compose.yaml`, and a
`DockerRunLauncher` giving each run its own container. Taking them in order:

1. **Answered by the shape, not by argument.** The image's `CMD` is
   `["just", "serve", "3000", "8081", "0.0.0.0"]`, so it restates the
   *toolchain* — a base image, Node, uv, the DuckDB extensions, the dbt manifest
   — and never the service. The fifth restatement the reason predicted did not
   appear, because there was nothing to restate. What a workflow guard could not
   cover, `tests/test_dagster_instance.py` does instead: it reads the Dockerfile
   and `compose.yaml` as data and holds them against `deploy/dagster.yaml` —
   every launcher `env_vars` name assigned, every run-container volume declared
   and mounted at the same path, the network matching, the run image matching
   the service's.
2. **Conceded, and then answered.** It really is a new pinning surface: four
   base and service images. `.github/dependabot.yml` gained a `docker`
   ecosystem for the Dockerfile beside the `docker-compose` one for the compose
   file, and the test above refuses a tag Dependabot could not bump — `latest`,
   a bare name, or a floating `X.Y` where upstream's exact tag is `X.Y.Z`. The
   count of versions that can only age deliberately is unchanged at three.
3. **Largely dissolved — by §3's catalog move, not by the container.** With
   `LAKEHOUSE_CATALOG` in Postgres and `LAKEHOUSE_DATA_PATH` in a bucket, the
   landing zone is not a file any more, and the only thing a run container and
   the service both open is `data/warehouse.duckdb`. "Two containers sharing a
   volume reintroduce the lock" was measured again under that arrangement and
   is now the narrow case the one-run queue already covers — see the
   measurements below. The reason was right about the mechanism and was
   answered by moving the state, which is the thing worth remembering.
4. **Holds exactly as written.** nginx serves the `mds_site` volume read-only
   and never opens the warehouse. It is a static file server, containerised
   only because the rest of the stack already is.

### The recipe

Built. It lives in the [`justfile`](../justfile), which carries the reasoning
below in its comments. What follows is the **shape, abridged** — not a second
copy to drift against, and **not runnable as it stands**:

```just
serve dagster_port="3000" site_port="8081": where dbt-parse
    #!/usr/bin/env bash
    set -euo pipefail
    # … refuse to start if $SITE_ROOT holds no site …
    # … traps, so a signal exits 0 and a dead child exits 1 …
    uv run --group orchestration dagster-webserver -h 127.0.0.1 -p {{ dagster_port }} &
    uv run --group orchestration dagster-daemon run &
    uv run --group orchestration python -m http.server {{ site_port }} --directory "$SITE_ROOT" &
    # … record each PID, then `wait -n` …
```

**The shebang is not decoration, and pasting the block without its elided half
gives you the failure this section is about.** `just` runs a recipe that has no
shebang **one line per shell**, so each `&` backgrounds a process into a shell
that exits immediately and a trailing `wait` waits on nothing. Measured on a
reduced copy: such a recipe returns **exit 0 in under a second**, having
orphaned every child — a `just serve` that appears to have worked. The runnable
text is the justfile's, and it is the only copy.

`SITE_ROOT` defaults to `reports/build`, the fixed path
`publish/build_report.py` writes to, because §4's `current` symlink is not
built. That is the one place the recipe is smaller than the design, and it costs
what §4 says it costs: the site is *down* for the length of a rebuild, because
that module clears its output directory on every run, `--clean` or not.
**Measured on 2026-09-17**, polling two pages every 2 s through a `publish_site`
launched from the UI: both served 404 for 85 s, the whole of the 86 s
`reports/evidence_site` step, and came back together when it finished. It is a
404 from a server that is still up, not a refused connection, so a probe on the
port reports a healthy site through the whole outage. The recipe refuses to
start if the directory is not there at all, naming `just report`, rather than
serving 404s that look like a broken build.

Cold start on a warm venv is **17 s** from `just serve` to both ports answering,
`dbt deps` and `dbt parse` included — the dependency below is most of it.

Four things in that block are load-bearing:

- **`dagster-webserver` and `dagster-daemon`, not `dagster dev`.** Checked
  upstream rather than assumed, and in two places. The help text shipped with
  the pinned Dagster (1.13.19) describes the command as starting "a **local**
  deployment of Dagster, including dagster-webserver running on localhost and
  the dagster-daemon running in the background": local, and both in one
  invocation. The docs go further, listing what dev mode does not give you:
  authentication or web security, multiple webserver replicas, zero-downtime
  deployment, and **automatic daemon restart**. That last one is exactly what
  the systemd unit below supplies, which is the whole of why the split is worth
  making.

  **The wording to know about:** the explicit "intended for local development
  *only*" warning is written on the docs page about **`dg dev`**, the newer
  CLI's equivalent, and `dg` is a tool this project deliberately does not
  install. The reasons it gives are about process architecture rather than about
  which CLI typed them, and the shipped `dagster dev` help says "local
  deployment" in its own words, so the conclusion carries. But the sentence
  somebody will go looking for is not phrased about the command used here.
- **`dbt-parse` as a dependency, not an afterthought. Measured, and it is the
  one thing that actually breaks.** `prepare_if_dev()` in
  [`orchestration/resources.py`](../orchestration/resources.py) fires only under
  the dev CLI, which sets `DAGSTER_IS_DEV_CLI`, so running the webserver directly
  does not prepare the dbt project. Both halves were run:

  | With `dbt/target/manifest.json` | webserver + daemon, separately | `dagster dev` |
  |---|---|---|
  | present | code location loads (`RepositoryLocation`), daemon alive | loads |
  | **absent** | **code location fails** (`PythonError`), webserver still answering | **regenerates the manifest** |

  The failure is legible, which is the good news:
  `dagster_dbt.errors.DagsterDbtManifestNotFoundError: …/dbt/target/manifest.json
  does not exist.` The trap is that **the webserver comes up healthy either
  way**: it answers HTTP and the daemon keeps running, and only the code
  location inside it is broken. A liveness check on the port would call this
  deployment fine. `dbt/target/` is gitignored, so it bites on every fresh
  deploy; the justfile already records the prerequisite at every headless recipe,
  and §10 makes it a step.
- **`--group orchestration` on the Dagster processes; on the file server it is
  harmless.** `uv run` only ever *adds* what its groups need and never removes a
  package (measured 2026-09-11 on uv 0.12.12: a bare `uv run` left Dagster
  installed). What does strip the venv under a running service is a bare
  `uv sync`: `default-groups` is deliberately unset in `pyproject.toml`, so it
  syncs to `dev` alone, and `uv sync --dry-run` on this tree would uninstall 46
  packages, `dagster`, `dagster-webserver` and `grpcio` among them. The
  already-running webserver and daemon would survive on imports they hold in
  memory while everything they *fork later* dies — the grpc code servers, and the
  run worker forked per schedule tick. The ports answer, `wait -n` never returns,
  and nothing materialises: §2's own failure mode, arriving through the
  dependency resolver. §8 records it. (An earlier version of this section blamed
  `uv run`, on the evidence of that `uv sync` dry run.)
- **The port collision.** `just dagster` uses 3000 and so does `evidence dev`.
  The site here is static, so it is served by anything; give it its own port and
  do not reach for `evidence dev`, which is a hot-reloading dev server.

### Stopping it — measured

`trap 'kill 0' EXIT` and a bare `wait` are what this section proposed, and
building it corrected both. The tree is also bigger than the recipe starts:
**twelve processes, not three**, because the webserver and the daemon each spawn
a `dagster api grpc` code server, which spawns a multiprocessing resource tracker
of its own. Whether the cleanup reaches all of that is a question rather than a
formality, so it was run — five ways of stopping it, against the real graph:

| stopped by | processes | left behind | `just` exits |
|---|---|---|---|
| Ctrl-C (SIGINT to the process group) | 12 | **0** | 130 |
| `systemctl stop` (SIGTERM to the group) | 12 | **0** | 143 |
| SIGTERM to `just` alone | 12 | **0** | 143 |
| SIGTERM to the recipe's shell alone | 12 | **0** | 0 |
| **one child killed** (`kill -9` on the site server) | 12 | **0** | **1** |

Three corrections came out of that, and the first one matters most:

- **`kill` the recorded PIDs, not `kill 0`.** Measured separately: `uv run`
  forwards SIGTERM to the process it spawned, and the cascade carries on down to
  the grpc servers, so three PIDs are enough to stop twelve processes. `kill 0`
  signals the whole group *including the recipe's own shell*, which would then
  die **by SIGTERM** — and `man systemd.service`, read on the systemd this was
  measured against (259), lists SIGHUP, SIGINT, SIGTERM and SIGPIPE as
  *successful* termination alongside exit 0. So the proposed line would have quietly disabled the
  `Restart=on-failure` written four paragraphs below it: the unit would exit
  looking clean and never come back.
- **`wait -n`, not `wait`.** Plain `wait` returns only once *every* child has
  exited, so a dead webserver leaves the recipe running, systemd seeing a healthy
  unit, and nothing materialising. That is this section's own "the port answers
  and the code location is dead", one level out. The last row is the fix
  working: killing one child takes the other eleven processes with it and exits
  non-zero, which is the only thing that makes `Restart=on-failure` mean
  anything.
- **A signal and a dead child must not look alike.** The bottom two rows are the
  same shutdown with different causes, and a supervisor reads the difference, so
  the recipe traps INT and TERM to exit 0 and lets a child's own exit fall
  through to 1. Where the signal reaches `just` too — Ctrl-C, and systemd's
  default `KillMode=control-group` — `just` dies by the signal instead and
  systemd reaches the same verdict by the other route, which is why those rows
  are 130 and 143 rather than 0.

### Reading its log — measured

`just serve`'s output interleaves the three processes with every run's own
output, dbt's and Evidence's included. Watched through a scheduled
`full_refresh`, one launched from the UI, `load_retail` and `publish_site` on
2026-09-17 (Dagster 1.13.22), it carries three lines that look like faults:

- **`dagster.code_server - WARNING - No heartbeat received in 20 seconds,
  shutting down`, about once a minute whether or not anything is running.** The
  daemon reloads its workspace every 60 s (`RELOAD_WORKSPACE_INTERVAL` in
  `dagster/_daemon/controller.py`) by starting a fresh code server, and the one
  it replaced stops receiving heartbeats and logs this 20 s later. What the
  warning sets is `_shutdown_once_executions_finish_event`
  (`dagster/_grpc/server.py`), so a retired server outlives the runs it launched.
  That was watched, not only read: a server retired 41 s before a `full_refresh`
  finished — by the reload timings, the one current when that run was launched —
  and the run completed. Of the
  code servers `pgrep` shows, the ones with `--heartbeat-timeout 20` are the
  daemon's, one current and one draining; the webserver's has 45 and lives as
  long as the webserver.
- **`QueuedRunCoordinatorDaemon - INFO - 1 runs are currently in progress.
  Maximum is 1, won't launch more.`, every 5 s for the length of every run**,
  whether or not anything is queued. It is §5's one-run limit being checked, not
  a run being refused.
- **dlt's `UserWarning: XDG_DATA_HOME is set to … but ~/.dlt already exists.
  Using ~/.dlt`**, at every code load and ingest. On a laptop it is noise. **On a
  service host it is the line to act on:** it says §10's `XDG_DATA_HOME` is being
  ignored because the service user has a `~/.dlt`, so the watermark lives there
  rather than on the volume step 2 named (§3).

`publish_site` adds a fourth from Evidence, `Column "last_revised_at" … contains
only null values so it has been cast to Float64`, which means no snapshot has
recorded a revision yet (`building-evidence-reports`).

### The supervisor

`just serve` dies with the SSH session. That is a systemd unit's job, not a
recipe's, and the layering keeps the recipe as the single definition of *what*
runs while the unit supplies restart, boot and logging:

```ini
[Unit]
Description=modern-data-stack
After=network-online.target

[Service]
Type=exec
User=mds
WorkingDirectory=/srv/mds/repo
EnvironmentFile=/srv/mds/service.env   # the paths in §3 — and nothing secret; see §6
ExecStart=/usr/bin/just serve
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

`[Install]` is what `systemctl enable` needs; without it the unit starts by hand
and never at boot. `Type=exec` rather than `simple` so a failure to execute
`just` is reported at start time instead of appearing to succeed.

## 3. The state that must outlive a restart

Everything below has to be on durable storage, and each row fails differently:

| Path | Why it is state | What losing it costs |
|------|-----------------|----------------------|
| `data/lakehouse/` | dlt's landing zone, and the only copy of every raw table | the weather archive cold-starts at three years: days of Open-Meteo budget, gone silently |
| `data/warehouse.duckdb` | the `history` schema only; every other schema is derived | the revision log, permanently. No rebuild invents a version upstream has overwritten |
| dlt's data dir | the WDI watermark and the ECB's last fixing | a silent full re-fetch, or a five-year window into a warehouse with no history |
| `.dagster/` | the laptop instance: run and event storage (SQLite), plus **schedule on/off state**; its `dagster.yaml` is config, checked in, and has to be carried to any other `DAGSTER_HOME` | run history, and a service that looks running and ingests nothing (§5); without `dagster.yaml`, runs no longer queue behind each other (§5) |
| the `dagster` database | the same three, when `DAGSTER_HOME` names `deploy/` instead: `deploy/dagster.yaml` puts run, event and schedule storage in Postgres, so they outlive a container that is replaced rather than restarted | the same three losses, with nothing left on a filesystem to restore them from |
| `$DAGSTER_STORAGE_DIR` | compute logs and the artifacts a run writes, under `deploy/dagster.yaml` — Postgres storage does not take these. Only that file reads the variable: the laptop instance keeps them in `.dagster/storage/`, inside the `.dagster/` row | a finished run whose logs the UI shows as empty |
| `data/cache/` | the retail workbook | a download, never data |

**The dlt row is the one a service gets wrong**, and the reason is written into
`build_pipeline()` already: that directory is `~/.dlt/pipelines/<name>/` **if
`~/.dlt` already exists**, and `$XDG_DATA_HOME/dlt/pipelines/<name>/` otherwise.
It resolves from `$HOME`. Run the service under a system user whose home is not
the developer's and the watermark is simply not there: no error, no warning,
just a full re-fetch on the first run and a five-year window on the second.
`XDG_DATA_HOME` pointed at the durable volume is the lever, and it belongs in
the unit's environment file next to the other paths.

The rest are the environment variables the code already reads, all absolute:
`PROJECT_ROOT`, `WAREHOUSE_PATH`, `LAKEHOUSE_DIR`, `INGEST_CACHE_DIR`,
`DAGSTER_HOME`. `modern_data_stack.paths` is the one resolver behind all of them,
which is what makes a service configurable at all. See
[`docs/REUSING_THIS_STACK.md`](./REUSING_THIS_STACK.md#4-invariants-that-fail-silently).

## 4. Publish-and-swap

**The service's build cycle is `release-data.yml`'s shape with the publishing
taken out**, and every piece it keeps already exists in `publish/`. Build into a
scratch warehouse; swap it in only if it passes.

What it borrows is the **carry-forward**: `restore_history` and the "did not
shrink" check. Those are not publishing: they exist because `history` and
`raw.om_weather_daily` are state no rebuild reproduces, and a service that
rebuilds nightly needs them *more* than a monthly release does, not less. What it
leaves behind is everything downstream of that: `export_warehouse`, the Parquet
fan-out, `SHA256SUMS`, attribution, the storage-format ceiling and the salt. A
release is an outward-facing act with obligations attached; a swap is an internal
one.

1. **Carry the unreproducible state in.** `publish/restore_history.py` copies
   `CARRIED` out of the *live* warehouse into the scratch one. The recipe already
   takes a path argument (`just restore-history <path>`), so this is a new
   argument, not new code.
2. **Build.** `WAREHOUSE_PATH=<scratch> just materialize`. The dbt tests and the
   blocking asset checks gate it exactly as they gate a release, so a warehouse
   that fails its own quality gates never reaches the swap.
3. **Verify** that the carried state did not shrink, with
   `modern_data_stack.history.carried_rows`, the same rules the restore used,
   so a relation added to `CARRIED` reaches the check too.
4. **Swap.** `rename(2)` the scratch warehouse over the live one, then build the
   site and flip a `current` symlink at the served directory (`ln -sfn`, atomic
   via rename).

**Dagster is the trigger for all four steps, and there is no second scheduler.**
The swap is an asset on the end of the graph, not a wrapper around it, and two
facts make that work:

- **`rename(2)` does not need the lock**, and nothing in the run holds the
  warehouse across steps anyway: `_scalar` and both warehouse-reading asset
  checks open a connection and close it in a `finally`. So a swap asset
  downstream of `analytics/pipeline_status` finds the file quiescent.
- **The build path is fixed at daemon start, not chosen per run.**
  `transform/co2_intensity.py` binds `DUCKDB_PATH = warehouse_path()` **at
  import**, and `orchestration/assets.py` imports that constant, so the code
  location reads `WAREHOUSE_PATH` once when it loads. A run cannot vary it. It
  does not need to: point the daemon's `WAREHOUSE_PATH` at the build file, let
  every run write there, and let the swap asset promote it to the path readers
  know. `reports/evidence_site` then depends on the swap, so the site is built
  from the promoted file.

### The swap semantics — measured

A rename over an open database is the step the design rests on, so it was run
rather than reasoned from POSIX. Two throwaway databases, `v1` served and `v2`
built, each with a 400,000-row table so pages are read lazily rather than
slurped on connect:

| | Result |
|---|---|
| `rename(2)` while a reader holds the served file | **succeeds**; the inode changes under it |
| the held reader, afterwards | still answers, still `v1`, all 400,000 rows and the checksum intact |
| a **new** reader, separate process | sees `v2` immediately |
| a **writer**, separate process, stale reader still open | **can open the live path**; the stale reader's lock is on the old, now-unlinked inode |

So a swap mid-query gives a reader a consistent *old* database rather than a torn
new one, and the next cycle is not held hostage by whoever forgot to close a
session. That last row is §8's lock nuisance genuinely dissolving rather than
merely being avoided.

**Re-running this needs separate processes, and the first attempt got it
wrong.** DuckDB's Python client caches an instance per path within a process, so
asking it for a "new" connection to the swapped path returned the *old* one,
reporting `v1` after the swap, and then refused a writer with `Can't open a
connection to same database file with a different configuration`. Both answers
looked like filesystem findings and neither touched the filesystem. Open the
second connection in a subprocess.

Three properties make this worth the machinery:

- **The live warehouse becomes read-only by construction.** Nothing writes to it
  between swaps. §8's lock problem stops applying to every reader outside the
  build: ad-hoc `just sql`, inspection, a future query API.
- **A failed build never destroys a good warehouse.** Today a red `dbt build`
  leaves a half-written file where the good one was.
- **The previous site stays up during a rebuild**, which the fixed
  `reports/build/` path cannot do on its own: `publish/build_report.py` clears
  that directory on every run, `--clean` or not.

### Pointing the site at a scratch warehouse — measured

`reports/sources/warehouse/connection.yaml` hardcodes a path and reads no
environment variable, so the obvious question is whether an Evidence build can be
redirected the way every other layer can. It can, with a trap:

- **`EVIDENCE_SOURCE__warehouse__filename` overrides `connection.yaml`.**
  Confirmed against the installed Evidence at both layers: `loadSourceConfig`
  merges the environment *over* the file, and a full `evidence sources` run
  against an empty scratch database failed on every table, which is the
  extraction genuinely reading somewhere else.
- **An absolute path is silently made relative.** The DuckDB connector does
  `path.join(sourceDirectory, filename)`, and `path.join` does not respect a
  leading slash, so `/srv/mds/scratch/warehouse.duckdb` was opened as
  `reports/sources/warehouse/srv/mds/scratch/warehouse.duckdb`. The error names a
  path nobody typed, which is the same failure shape as `LAKEHOUSE_DIR`'s. **The
  override has to be relative to `reports/sources/warehouse/`**, the same form
  the committed value already uses.
- **Evidence opens the file `READ_ONLY`**, so a site build never takes the
  writer lock. That is what makes the ordering below a free choice rather than a
  constraint.

**Recommendation: build the site *after* the swap, and skip the override.**
Evidence then reads the live warehouse at its committed path and there is one
fewer moving part. The cost is that a warehouse is live for the duration of a
site build before its pages are rendered, which the dbt tests have already
gated. Use the override only if that window matters.

## 5. The schedule: bootstrap is not steady state

Two facts combine into this repo's collected failure mode, a service that looks
running and is not:

- **`daily_refresh` ships `STOPPED`**, deliberately: opening the UI should not
  start hammering public APIs on a timer. Starting it is **instance state in the
  schedule storage**, not code, and that was measured rather than assumed. A
  fresh instance reports `daily_refresh [STOPPED]`, `dagster schedule start`
  flips it to `[RUNNING]`, a *separate process* reading the same instance reads
  that back, and a `schedules/` directory appears under `DAGSTER_HOME`. Pointed
  at a different `DAGSTER_HOME` the same schedule is still `STOPPED`, which is
  the same fact from the other side. So it survives a restart only if that
  storage is durable, and a wipe silently returns the service to ingesting
  nothing. Flipping `default_status` instead is a code change that changes what
  `dagster dev` does for everyone who clones the repo.

  Under `deploy/dagster.yaml` the schedule storage is Postgres rather than
  SQLite, so the durable thing is the `dagster` database and `DAGSTER_HOME` holds
  no schedule state at all — measured 2026-09-17: starting the schedule against
  that instance wrote a `RUNNING` row to `instigators` and left every file under
  `.dagster/` byte-identical. The failure mode is unchanged, only relocated: drop
  the database and the service comes back up ingesting nothing.
- **It targets `full_refresh` only, which excludes two things, and the second
  one only started mattering when §2 became real.** It excludes `load_retail`:
  correct forever on an established lakehouse (retail is a closed archive whose
  partitions are replayed by hand), and a failure on a fresh one, inside
  `stg_retail_lines`, with `Catalog Error: Table with name retail_invoice_lines
  does not exist!`. **It also excludes `reports/evidence_site`.** That cost
  nothing while the site was a Pages deploy on its own workflow; with `just
  serve` in front of it, only `publish_site` builds the site and *nothing
  schedules `publish_site`*, so a scheduled service keeps the warehouse current
  and leaves the dashboard exactly where the last `just report` left it — no
  error, no log line, a page that simply stops moving. The exclusion is
  deliberate and stays (three workflows run `full_refresh` on a bare uv checkout
  with no Node), so the answer is not to move the asset into the job but to
  refresh the site alongside the graph: step 6 of §10 by hand, and §4's swap
  properly.

So **the service's first run is a different command from its steady state** —
§10 is the runbook that says so, rather than leaving it to be discovered on a
rebuilt host at 06:00 UTC.

**`daily_refresh` is the only scheduler**, and adopting §4 does not change that.
The swap is an asset inside `full_refresh`, so the schedule that runs the graph
runs the swap with it; there is no systemd timer and no second cron. What
adopting §4 changes is one environment variable (`WAREHOUSE_PATH` points at the
build file rather than the served one) and one asset on the end of the graph.

**Without §4 the schedule still works**, materialising into the served warehouse
in place. That is the smaller starting point and it costs three things. Two were
here from the start and neither is silent: a reader lockout for the length of a
build (§8), and a half-written file where the good one was if the build goes
red. **The third arrived with `just serve` and is silent** — the site is served
from a fixed `reports/build/` that only a manual `just report` rewrites, so the
dashboard ages while the warehouse behind it does not. All three are survivable
on an internal deployment; only the third needs somebody to remember.

### Missed ticks, and one run at a time — measured

**A daemon that starts after a missed tick launches it at once.** Measured on
2026-09-17, with `daily_refresh` `RUNNING` in this instance and the service down
across more than one 06:00 UTC tick: `just serve` started at 11:52 UTC, and
within 16 s the daemon logged `daily_refresh has no partition set, so not trying
to catch up` and launched a `full_refresh` for that morning's tick. Only the
latest missed tick runs — `dagster/_scheduler/scheduler.py` drops the rest for a
schedule with no partition set. A restart eighteen minutes later launched
nothing, because that tick was already recorded. So a host rebooted, or
restarted by systemd after a crash, any time after 06:00 UTC starts a full build
while whoever restarted it is opening the UI, and a Materialize click in that
window used to be a second writer against a file DuckDB lets one process write
(§8).

**`.dagster/dagster.yaml` now holds the instance to one run in progress**
(`concurrency: runs: max_concurrent_runs: 1`; Dagster's default is 10). Measured
against a throwaway instance whose only job sleeps, so no warehouse was involved:
under the previous file two launches were both `STARTED` within 5 s, and under
this one the second stayed `QUEUED` until the first finished. The file is read
at process start, so the live service reported the new value only after a
restart.

**The queue governs what enters it, and no recipe that materialises enters it.**
The UI, its backfills included, and the schedule submit to it. `dagster job
execute` (`just materialize`, `materialize-site`) and `dagster asset materialize`
(`materialize-select` and both `backfill-*` recipes) execute in the calling
process: on the same throwaway instance, two overlapping `dagster asset
materialize` runs both started at once, and neither was ever enqueued. So
`just backfill-weather`, paced at about an hour a decade, runs beside a scheduled
`full_refresh` rather than behind it. The two directions differ, and the same
instance measured both for `just materialize`:

| | Result |
|---|---|
| launched from the UI while `just materialize` runs | **waits.** The queue counts every in-progress run in its instance however it started (`2 runs are currently in progress. Maximum is 1`), and launched the queued run 4 s after the in-process one ended, 14 s after the other queued run had |
| `just materialize` while a UI or scheduled run is going | **starts at once**, beside it |

So on a service host, start work from the UI rather than from the recipes. And
the queue only sees runs recorded in its own `DAGSTER_HOME`: the justfile
defaults that to the checkout's `.dagster/`, so a recipe typed in a shell that
has not loaded §10's environment file is invisible to the service's queue in both
directions.

## 6. Exposure

**Dagster's webserver has no authentication.** Bind it to localhost and put
whatever the host already terminates TLS with in front of it. The Evidence site
is static and safe to expose; note that it ships the underlying Parquet to the
browser, so "the site is public" means "these tables are public".

**The service held no secrets until it had backing services, and that was a
consequence of §4 rather than a happy accident.** Every source it reads is public
and unauthenticated, and `PII_SALT`, the one secret in the whole project, belongs
to the export, which the service does not run.

**The compose stack ends that, and adds something worse than a secret.** In
order:

- **`PGPASSWORD`** is a real credential on the host, in `.env`, read by both
  `just` and `docker compose`. It is deliberately never in a URL — libpq reads
  it from the environment, so it stays out of `LAKEHOUSE_CATALOG`, out of
  `dbt/profiles.yml` and out of every process list — but it is on the machine,
  and the `dagster` service passes it to every run container it launches.
- **The docker socket is mounted into the `dagster` container, and that is
  root-equivalent on the host.** `DockerRunLauncher` needs it to start run
  containers; anything that can reach it can start a container with any mount it
  likes. This is a larger grant than the password and is the reason the service
  binds 127.0.0.1. A deployment that puts a reverse proxy in front of Dagster is
  exposing a docker socket by proxy, so the authentication §6 opens with stops
  being optional.
- **The container runs as root**, which the socket makes close to moot: a
  non-root user in the container that can write the socket has the host anyway.
  Recorded rather than fixed, so the trade is visible.
- **`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`** are credentials in the same
  sense, though the ones in `.env.example` reach a SeaweedFS bound to localhost.
  A real object store makes them the third real secret. Its environment file is paths. If publishing is ever
added to the host, that stops being true immediately: the salt has to be
**stable across runs** (a fresh one repseudonymises every customer for no change
in the data), so it would become a long-lived secret sitting on a machine that
also serves traffic. That is the trade to weigh, and it is the reason releases
are left on GitHub here. [`docs/DATA_PROTECTION.md`](./DATA_PROTECTION.md) has
the reasoning.

**The pseudonymisation happens at the export and nowhere else**, so the
warehouse the service builds and serves from holds `customer_id` in the clear,
exactly as a local build does. The *site* is fine: the retail source queries
were pruned during the classification work and now only aggregate over that
column (`count(distinct …)`, `… is null`), so no Parquet reaching a browser
carries an identifier. The exposure is therefore the **file**, not the pages: do
not serve `data/warehouse.duckdb` itself, and treat a shell on the host as access
to the personal column. A deployment that wants to hand the database out needs
the export path, and with it the salt.

## 7. What this does not solve

A service on one host changes none of the ceilings.
[`docs/FOR_REVIEWERS.md`](./FOR_REVIEWERS.md#4-what-breaks-at-1000) already
covers them: the Polars step's in-memory rank, the single-writer lock and the
`quack` route off it, full-refresh materialisation, and the point where shipping
Parquet to the browser stops working. Publish-and-swap removes the lock's
*operational* nuisance without raising its ceiling; one process still does every
write.

Named absences, so they are decisions rather than oversights: no multi-tenancy;
no authentication; no health endpoint, though `analytics.pipeline_*` is already
the health data one would expose and `reports/pages/pipeline.md` already renders
it; and alerting still routed through `nightly.yml`'s GitHub issue unless a
Dagster sensor replaces it.

## 8. Invariants that fail silently in a service

The list [`docs/REUSING_THIS_STACK.md`](./REUSING_THIS_STACK.md#4-invariants-that-fail-silently)
keeps, extended with the ones only an always-on deployment meets:

- **dlt's data dir moves with `$HOME`.** A different service user resets the
  watermark with no error (§3).
- **`prepare_if_dev()` does not fire outside `dagster dev`.** The code location
  fails to load, or loads against a stale manifest (§2).
- **A `STOPPED` schedule survives a `.dagster/` wipe as stopped.** The service
  runs, serves an increasingly old site, and ingests nothing (§5).
- **A run container's command is `dagster api execute_run`, not a `just`
  recipe.** Everything else in the image reaches the venv through `uv run`, so
  nothing in the justfile needs `/app/.venv/bin` on `PATH` — and the launcher
  does. Without it a run dies before any Python runs, with
  `exec: "dagster": executable file not found in $PATH`; `auto_remove` then
  deletes the container, so the only evidence is one `ENGINE_EVENT` in the event
  log. Measured 2026-09-17, and the reason the Dockerfile's `PATH` is what it
  is.
- **An `env_vars` name the launcher cannot resolve fails at *launch*, not at
  start.** A bare name in that list means "copy this from my environment", and
  `parse_env_var` raises when it is unset — inside the daemon, dequeuing the
  run, after the UI has already said the run was launched. The service is
  healthy the whole time. `tests/test_dagster_instance.py` holds every name in
  that list against `compose.yaml`'s `dagster` environment and the Dockerfile's
  `ENV`, because a startup check could not.
- **Without `auto_remove`, every finished run leaves an exited container.** They
  are invisible in `docker ps` and accumulate for as long as the service runs.
  It is set in `deploy/dagster.yaml`; the cost is that a run container that
  failed to *start* also disappears, taking its `docker logs` with it, which is
  why the previous bullet's failure has to be read out of the event log.
- **The site is down for the copy, not for the build.** `evidence build` adds to
  its output directory rather than replacing it, so `publish/build_report.py`
  empties that directory first — which cannot be the nginx mount point, because
  `rmtree` on one fails with `EBUSY`. So the build happens in `reports/build`
  and the result is copied to `SITE_ROOT` afterwards, replacing its *contents*.
  The outage is the copy. Measured 2026-09-17 in the compose stack: `just report`
  in the container built 11 pages / 482 files / 92 MB and copied them to the
  volume, and **65 one-second polls of nginx through the whole build returned 200
  every time** — the copy window did not last a second. Against the 85 s of 404s
  the in-place build produced (§2), that is the whole point of the indirection.
  A build that fails leaves the previous site serving, because nothing is copied
  until it succeeds.
- **The Postgres init script runs once, on an empty data directory.** Adding a
  database to `deploy/postgres/init.sql` does nothing to a volume that already
  exists — the entrypoint only runs `/docker-entrypoint-initdb.d` when it is
  initialising. `just compose-down volumes` is the reset, and it destroys the
  catalog and the bucket with it.
- **A container keeps the healthcheck it was created with.** Editing
  `compose.yaml`'s `healthcheck` and running `just compose-up` changes nothing
  for a running container; it has to be recreated.
- **A code location name is part of a schedule's identity, and `-m` changes
  it.** `dagster schedule start daily_refresh -m orchestration.definitions`
  prints `Started schedule daily_refresh` and writes a row the running service
  never reads: the instigator's selector id hashes the *code location name*, and
  `-m` names the location after the module while `pyproject.toml`'s
  `[tool.dagster]` names it `modern_data_stack`. Measured 2026-09-17 against the
  deployed instance: the two spellings put **two `RUNNING` rows for one
  schedule** in `instigators`, and the bare `dagster schedule list` — which
  resolves the location the same way the webserver and daemon do — still read
  `[STOPPED]`. The same mismatch fails a run *after* the CLI reports success:
  `dagster job launch -j load_retail -m orchestration.definitions` returned 0,
  and the daemon then marked the run `FAILURE` with
  `DagsterCodeLocationNotFoundError: Location orchestration.definitions does not
  exist in workspace`. Dropping `-m` made the same launch succeed. **Against a
  service, never pass `-m`**: let the CLI fall back to `[tool.dagster]`, which is
  what the service itself falls back to.
- **A `DAGSTER_HOME` outside the checkout has no `dagster.yaml`.** §10 puts it on
  the durable volume, where Dagster finds no config, prints one notice at start
  and falls back to its defaults — ten runs at once rather than one, so a restart
  after a missed tick plus one click is two writers again (§5). Measured: an
  empty `DAGSTER_HOME` reports `max_concurrent_runs` as 10, and one holding a
  symlink to the checked-in file reports 1.
- **`dagster instance info` prints `compute_logs: NoneType` for a configured
  compute log manager.** Measured 2026-09-17 on `deploy/dagster.yaml`: the line
  says `NoneType` while the instance's manager really is a `LocalComputeLogManager`
  writing to `$DAGSTER_STORAGE_DIR`. It is a display quirk of that command and
  not a config that failed to load — read it back off the instance rather than
  out of `instance info` before changing anything to chase it.
- **The recipes do not queue behind the service.** `just materialize` and
  `materialize-site` run `dagster job execute`, and `materialize-select` and both
  `backfill-*` recipes run `dagster asset materialize`; each executes in its own
  process, beside whatever the service is running, and only the other direction
  waits (§5).
- **A bare `uv sync` uninstalls Dagster.** `default-groups` is unset, so
  `uv sync` without `--group orchestration`, typed against a *running* service,
  strips 46 packages out of the venv under it. The running processes hold their
  imports and keep answering; the grpc code servers and run workers they fork
  afterwards do not exist any more. `uv run` does not do this — it only adds
  packages — so the recipes are safe; stop the service before a `uv sync`, or
  give it its own checkout (§2). The `deploy` group is the same trap one group
  further out: against a venv built by `just deploy-deps`, a
  `uv sync --group dev --group orchestration` would uninstall `dagster-postgres`
  and `psycopg2-binary` (measured `--dry-run`, 2026-09-17), and the next restart
  of a `deploy/` instance fails on its own storage config.
- **The schedule refreshes the warehouse and never the site.** `daily_refresh`
  targets `full_refresh`, which excludes `reports/evidence_site`; only
  `publish_site` builds it and nothing schedules that. So a host that follows §10
  serves a dashboard frozen at bootstrap over a warehouse that updates daily,
  indefinitely, with no error anywhere (§5).
- **`LAKEHOUSE_DIR` must keep one absolute spelling for the deployment's whole
  life.** DuckLake compares `data_path` as a *string*, so moving the volume's
  mount point refuses every attach, and the error surfaces inside `dbt build`,
  one layer below whatever chose the spelling.
  - **A catalog remembers the data path it was created with, so one catalog
    schema cannot serve two arrangements.** Hit for real on 2026-09-17: the
    compose stack was pointed at a Postgres catalog that an earlier laptop run
    had already initialised with the Parquet *on disk*, and the first run in a
    container failed with `DATA_PATH parameter "s3://lake/modern-data-stack/"
    does not match existing data path in the catalog "/home/…/data/lakehouse/data/"`.
    Nothing was wrong with either side. A laptop that shares a database with the
    compose stack wants its own `LAKEHOUSE_METADATA_SCHEMA`, or the same data
    path as the stack.
- **An Evidence `filename` override is joined onto the source directory.** An
  absolute path is not rejected, it is relocated (§4).
- **DuckDB is one writer XOR many readers, across processes.** Measured on the
  pinned 1.5.5, in both directions: a read-only connection fails while another
  process holds the file read-write, and a writer fails while a read-only
  connection is open. So a forgotten interactive session blocks the next
  scheduled build, and the build blocks every reader, which is the strongest
  argument for §4, where the served warehouse is never written to at all.

## 9. What is still unmeasured

The standard this repo holds itself to is that a claim in the docs was measured.
One thing here was not, and it is the one that cannot be measured without
building the design first:

- **A full swap cycle end to end**, timed against the ≈94 s stage baseline in
  [`docs/FOR_REVIEWERS.md`](./FOR_REVIEWERS.md#3-what-does-a-run-cost-and-how-long-does-it-take).
  The swap adds a restore, a verify and two renames to a run that is 65%
  network, so the expectation is that it disappears into the noise. But the
  restore copies `history` and the weather archive, which is real I/O, and
  nobody has timed it.

## 10. Standing it up

The ordered version of everything above. Written as a runbook because §5's two
facts are only dangerous out of order. `just serve` is real now, so steps 1, 3,
5 and 6 are things you can type; step 2's environment file and step 4's unit are
still to be written, and the whole of it assumes §4's swap asset has not been
built — which is the smaller starting point §5 describes, not a blocker.

**1. The host, once.** Clone, then `just setup`, which syncs the venv and
fetches the DuckLake extension, the one dependency no lockfile can name. Install
`just` itself (`uv tool install rust-just`). **Node is required**, because
`just serve` serves the Evidence site and refuses to start without a built one —
the graph itself still does not touch it, so this is a cost of serving rather
than of running.

**2. The paths, once.** Write `/srv/mds/service.env` with the §3 set, all
absolute, and nothing secret in it:

```sh
PROJECT_ROOT=/srv/mds/repo
LAKEHOUSE_DIR=/srv/mds/state/lakehouse
INGEST_CACHE_DIR=/srv/mds/state/cache
DAGSTER_HOME=/srv/mds/state/dagster
XDG_DATA_HOME=/srv/mds/state          # or dlt's watermark follows $HOME — §3
WAREHOUSE_PATH=/srv/mds/state/build/warehouse.duckdb   # the *build* file, §4
SITE_ROOT=/srv/mds/state/sites/current
```

`WAREHOUSE_PATH` is the one line that encodes the §4 decision. Point it at the
served file instead and the graph builds in place, which is the smaller starting
point §5 describes.

`DAGSTER_HOME` on the volume starts without the checked-in instance config, and
with it the one-run limit (§8). Link it rather than copy it, so it follows the
repo:

```sh
mkdir -p /srv/mds/state/dagster
ln -s /srv/mds/repo/.dagster/dagster.yaml /srv/mds/state/dagster/dagster.yaml
```

**Or point `DAGSTER_HOME` at `deploy/` instead**, which is the same decision
made the other way: the config is already in the repo, so nothing is linked, and
run, event and schedule storage go to Postgres rather than to the volume. It
needs `just deploy-deps` for the driver, the four `DAGSTER_*` lines and
`PGPASSWORD` from `.env.example`, and a reachable database — `just compose-up`
starts one, and `deploy/postgres/init.sql` creates the `dagster` database beside
the DuckLake catalog's. Then the unit's line is:

```sh
Environment=DAGSTER_HOME=/srv/mds/repo/deploy
```

`$DAGSTER_STORAGE_DIR` still belongs on the volume: compute logs and run
artifacts stay on a filesystem, and this is the instance that puts them there
(§3). Measured 2026-09-17:
the first use of that instance created 22 tables in the `dagster` database, and
after one `load_retail` the database was 9.3 MB.

**3. Bootstrap, by hand, before the service exists.** This is the step that
differs from steady state, and it differs because `daily_refresh` targets
`full_refresh`, which excludes `load_retail`:

```sh
just materialize     # load_retail, then full_refresh — in that order
just report          # the site `just serve` will serve; needs Node
```

Skip the first and the first scheduled run dies inside `stg_retail_lines` with
`Catalog Error: Table with name retail_invoice_lines does not exist!`. Doing it
by hand also means the first failure is watched rather than discovered in a log.
Skip the second and `just serve` refuses to start, naming the recipe — which is
the one failure in this runbook that cannot be quiet.

**4. The service.** `just serve` in the foreground is the whole thing already,
minus restart, boot and logging; the §2 unit is what supplies those and is the
part still to be written. Install it, then `systemctl enable --now mds`. Either
way, confirm the code location actually loaded, and do not skip this on the
grounds that the port answers:

```sh
uv run dagster definitions validate -m orchestration.definitions
```

**A broken code location does not take the webserver down.** Measured (§2): with
`dbt/target/manifest.json` missing, the webserver still answers HTTP and the
daemon still runs, while the only thing in the deployment that does any work
fails to load. A liveness probe on the port reports a healthy service that
cannot materialise anything. `definitions validate` is what distinguishes them,
and it names the cause: `DagsterDbtManifestNotFoundError`.

**5. Turn the schedule on. It is off until you do**, and this is instance state
in `DAGSTER_HOME`, not code, so it is also the step to repeat if that directory
is ever wiped:

```sh
uv run dagster schedule start -m orchestration.definitions daily_refresh
```

The UI toggle is the same action against the same instance; use either. What is
*not* equivalent is editing `default_status` in `definitions.py`, which changes
what `dagster dev` does for everyone who clones the repo.

**6. Refresh the site, and know that the schedule will not.** `daily_refresh`
targets `full_refresh`, which excludes `reports/evidence_site` (§5) — so the
warehouse moves daily and the served dashboard does not. Until §4's swap asset
exists, the site is rebuilt by hand:

```sh
systemctl stop mds     # or Ctrl-C the foreground `just serve`
just report
systemctl start mds
```

**Stopping first is not caution, it is required.** `evidence sources` opens the
warehouse to extract its Parquet, which is a reader against a file a scheduled
build may be writing: one writer XOR many readers, across processes. `just
report` is not a Dagster run, so the queue cannot hold it back. Only §4 avoids
it.

**The route that needs no stop is `publish_site` from the UI.** It enters the
one-run queue, so it waits for a scheduled run instead of reading beside it, and
its Evidence step reads the warehouse only after its own build has finished. It
costs a second full ingest and build — 174 s on 2026-09-17, against 91 s for
`full_refresh` alone — and the 85 s of 404s §2 measured.

**7. Verify, and prefer the checks that fail loudly.** `dagster schedule list`
shows it RUNNING, and `dagster instance info` run with the service's
`DAGSTER_HOME` prints a `concurrency:` block with `max_concurrent_runs: 1`; no
block at all means step 2's link is missing. After the first scheduled run, the
useful assertions are the ones the repo already computes rather than a glance at
the dashboard:
`analytics.pipeline_sources` for per-source load times and row counts,
`analytics.pipeline_tests` for anything failing, and the freshness policies in
the UI, which are the reason §1 calls the daemon the thing that makes the SLA
real.

**What can go wrong quietly, in the order it bites:** step 6 is forgotten and
the dashboard ages against a warehouse that does not, with nothing red anywhere;
a wiped `DAGSTER_HOME` leaves the schedule stopped and the service serving an
ageing site for the other reason; a stray bare `uv sync` strips Dagster out of
the venv while it is running; a `$HOME` change moves
dlt's watermark and re-fetches everything; a moved mount point breaks every
DuckLake attach because `data_path` is compared as a string; a `DAGSTER_HOME`
without `dagster.yaml` lets runs overlap again. All six are §8, and none of them
raises where you are looking.

### The compose variant

Everything above stands the service up on a host. This is the same service as
four containers, and it is the **recommended** way to run it — steps 1 to 4
collapse into two commands, because the image is the host setup and
`compose.yaml` is the environment file.

```sh
cp .env.example .env         # set PGPASSWORD; the rest has working defaults
just compose-build           # the image, mds:local
just compose-up              # postgres, seaweedfs, dagster, site
```

`127.0.0.1:3000` is Dagster and `:8081` is the dashboard. Then, once:

```sh
docker compose exec dagster uv run dagster job launch -j load_retail
docker compose exec dagster uv run dagster job launch -j full_refresh
docker compose exec dagster uv run dagster job launch -j publish_site
docker compose exec dagster uv run dagster schedule start daily_refresh
```

That is step 3's bootstrap and step 6, in the container. **Never pass `-m`** —
§8 says why. `just materialize` from the *host* is still not the service's
queue: it is a different process against a different warehouse entirely, since
the container's lives on the `mds_data` volume.

What differs from a host deployment, beyond packaging:

- **`restart: unless-stopped` is the supervisor.** It does what the §2 unit's
  `Restart=on-failure` and `WantedBy` do: a service whose `just serve` exits 1
  comes back, and all four return when the Docker daemon starts after a reboot
  — provided the daemon itself is enabled at boot. `docker compose stop` still
  stops them for good. It reaches only the four services; a run's container is
  the launcher's, not compose's, and is removed when it exits. The logging the
  unit supplied is `docker compose logs`.
- **Each run gets its own container**, launched by `DockerRunLauncher` from the
  same image, and removed when it finishes. Measured 2026-09-17: about 10 s from
  launch to a running run container, against a subprocess starting immediately.
- **The landing zone is not on a volume at all.** The catalog is in Postgres and
  the Parquet in SeaweedFS, so the one file a run container and the service both
  open is `data/warehouse.duckdb` on `mds_data` — which is why §2 reason 3 is
  largely dissolved rather than worked around.
- **Four named volumes**, and they are named explicitly because the run
  containers mount them by name from outside compose: `mds_data` (the warehouse
  and the landing-zone directory), `mds_dlt` (dlt's watermarks, at
  `DLT_DATA_DIR` rather than under `$HOME`), `mds_site` (what nginx serves), and
  `mds_dagster` (compute logs and run artifacts). Two more back the services:
  `mds_postgres` and `mds_seaweedfs`.
- **`just compose-down volumes` is the full reset**, and the only way to make
  `deploy/postgres/init.sql` run again. It destroys the catalog, the bucket, the
  warehouse and every run this instance recorded.

Verified end to end on 2026-09-17:

- `just compose-build` warm: **72 s**, image **2.15 GB**.
- `docker compose up -d --wait` on fresh volumes: healthy in **4 s**, with both
  databases created by the init script.
- `just compose-test-pipeline`: the whole fixture pipeline inside the image,
  **571 dbt nodes**, against the Postgres catalog and the SeaweedFS bucket, with
  its fixture schema created and dropped.
- `just run` in the container from fixtures: **56 s**, then `just report`
  built 11 pages and copied 482 files to the site volume, which nginx served
  without a single failed request.
- Two runs launched ten seconds apart: one run container, one `QUEUED` row, the
  second starting only when the first finished, and no exited containers left.
- Compute logs survived their containers: each run's `compute_logs/` directory
  was on the `mds_dagster` volume and readable from the service afterwards
  (5,293 bytes of stderr from a run whose container `auto_remove` had already
  deleted). That is the whole reason those two storages stay on a filesystem.
- `dagster definitions validate` inside the image with `--network none`: passes,
  which is what proves the manifest and `dbt_packages/` are baked rather than
  fetched.
- `docker compose stop dagster`: **1.0 s**, exit **143** (SIGTERM), well inside
  the ten-second grace and with no orphaned run container. `init: true` is what
  buys that — `just` is PID 1 and forks four children, and without an init to
  reap them the stop waits out the full grace period and exits 137.

And in CI, first run, 2026-09-17: the `container` job — cold image build,
services up, `just test-pipeline` inside it — took **2 m 14 s** end to end on a
`ubuntu-latest` runner. No layer caching was added, because the whole job is
faster than the threshold that would have justified one.

**Still unmeasured**, and §9's list should be read with these added: a
`publish_site` *run container* writing the volume (the copy was measured from
the service container instead), the cold-cache build time on a CI runner, and
any of this on a host that is not this laptop.
