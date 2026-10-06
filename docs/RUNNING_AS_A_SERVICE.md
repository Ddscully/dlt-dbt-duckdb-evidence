# Running this warehouse as a service

- **`just serve` is the service**: the Dagster webserver, its daemon and a static
  file server, supervised by systemd on a host or by compose in containers
  ([`just serve`](#just-serve-and-the-container-built-on-it)).
- **The compose stack is the recommended route**, with run, event and schedule
  storage in Postgres; the host runbook is the same service without containers
  ([Standing it up](#standing-it-up)).
- **The first run is a different command from the steady state**: bootstrap
  `load_retail` by hand, then start the schedule, which ships stopped
  ([The schedule](#the-schedule-bootstrap-is-not-steady-state)).
- **The schedule refreshes the warehouse and never the site**; rebuild the site
  yourself ([The schedule](#the-schedule-bootstrap-is-not-steady-state)).
- **Five kinds of state must be on durable storage**, and dlt's watermark
  follows `$HOME` unless told otherwise
  ([The state](#the-state-that-must-outlive-a-restart)).
- **The webserver has no authentication, and the compose stack mounts the
  docker socket**: bind to localhost ([Exposure](#exposure)).
- **Most of what goes wrong looks healthy**: a port that answers over a broken
  code location, a bare `uv sync` stripping Dagster from under a running service
  ([Invariants](#invariants-that-fail-silently-in-a-service)).
- **Building into a scratch warehouse and swapping it in is designed, not
  built**: [`PUBLISH_AND_SWAP.md`](PUBLISH_AND_SWAP.md).

Without `just serve`, the pipeline has two homes and neither is a service.
Locally it is `just run` or `just materialize`, invoked by a person. On GitHub
it is four workflows, triggered by a pull request, a push or a cron. The
`daily_refresh` schedule in
[`orchestration/definitions.py`](../orchestration/definitions.py) exists and
ships `STOPPED`, so nothing evaluates it.

"As a service" here means **one host, running continuously**: the asset graph
scheduling itself, the dashboard served without a deploy step, and the freshness
policies actually evaluated. Not multi-tenancy, not a query API, not a cluster.
[`docs/FOR_REVIEWERS.md`](./FOR_REVIEWERS.md#4-what-breaks-at-1000) covers where
this shape stops scaling, and none of that changes.

Named absences, so they are decisions rather than oversights: no multi-tenancy;
no authentication; no health endpoint, though `analytics.pipeline_*` is already
the health data one would expose and `reports/pages/pipeline.md` already renders
it; and alerting still routed through `live.yml`'s GitHub issue unless a
Dagster sensor replaces it.

## What it replaces

| Workflow | Under a service |
|----------|-----------------|
| `ci.yml` | **stays.** It is about the repo, not the data: fixture-backed, offline, per PR. A service has nothing to say about a pull request. |
| `live.yml` | **redundant**, if the service runs live daily and alerts. Its job is to distinguish "we broke it" from "OWID is down", and a service that ingests live inherits exactly that signal. |
| `pages.yml` | **redundant** if the service serves the site. Keep it only if the public mirror is wanted for its own sake. |
| `release-data.yml` | **stays, and the service deliberately does not do it.** GitHub is the distribution channel; the service is not. Publishing is a monthly, outward-facing act with its own obligations (attribution, pseudonymisation, a storage-format ceiling), and none of them get easier by moving to a host that is also serving traffic. See [publish-and-swap](PUBLISH_AND_SWAP.md) for what the service borrows from it and what it leaves behind. |

**The gain is not parity, it is that the SLA starts being enforced.** The
freshness policies in [`orchestration/assets.py`](../orchestration/assets.py)
(warn at two days without a load for `raw/*`, fail at seven; the modelled layers
rebuilt by 08:00 UTC) are declared in code and **evaluated by nothing** unless a
daemon is running. A schedule that quietly stopped firing is supposed to show as
a stale asset rather than as an absence somebody notices; that only happens with
a daemon running. See
[`docs/FOR_REVIEWERS.md`](./FOR_REVIEWERS.md#2-what-is-the-freshness-sla-and-what-happens-when-it-is-missed).

## `just serve`, and the container built on it

**`just serve` is the one definition of the service**: systemd supervises it on
a host, and the container stack runs it too, so the laptop default is still the
file and still `just`. What was weighed, including the case against a container
that the container then answered, is
[decision 0005](decisions/0005-just-serve-first-container-second.md). Four facts
hold it together:

1. **The image restates the toolchain, never the service.** Its `CMD` is
   `["just", "serve", "3000", "8081", "0.0.0.0"]`; the Dockerfile carries a base
   image, Node, uv, the DuckDB extensions and the dbt manifest.
   `tests/test_dagster_instance.py` reads the Dockerfile and `compose.yaml` as
   data and holds them against `deploy/dagster.yaml`: every launcher `env_vars`
   name assigned, every run-container volume declared and mounted at the same
   path, the network matching, and the launcher being the one that runs every
   run from the service's own image ([Standing it up](#standing-it-up)).
2. **The images are a pinning surface, and Dependabot watches it**: a `docker`
   ecosystem for the Dockerfile and `docker-compose` for the compose file. The
   instance test refuses a tag Dependabot could not bump — `latest`, a bare
   name, or a floating `X.Y` where upstream's exact tag is `X.Y.Z`.
3. **The single-writer lock belongs to the file, not the host.** With the
   catalog in Postgres and the Parquet in a bucket ([The state](#the-state-that-must-outlive-a-restart)), the only file a run
   container and the service both open is `data/warehouse.duckdb`, and the
   one-run queue serialises that ([The schedule](#the-schedule-bootstrap-is-not-steady-state)).
4. **The serving half needs no runtime.** `evidence sources` extracts the
   warehouse tables to Parquet under `reports/.evidence/`, `evidence build`
   renders static HTML into `reports/build/`, and the browser queries that
   Parquet with DuckDB-WASM. **The served site never opens
   `data/warehouse.duckdb`**, and nginx serves it read-only.

### The recipe

Built. It lives in the [`justfile`](../justfile), which carries the reasoning
below in its comments. What follows is the **shape, abridged** — not a second
copy to drift against, and **not runnable as it stands**:

```just
serve dagster_port="3000" site_port="8081" host="127.0.0.1": where dbt-parse
    #!/usr/bin/env bash
    set -euo pipefail
    # … refuse to start if $SITE_ROOT holds no site …
    # … traps, so a signal exits 0 and a dead child exits 1 …
    uv run --group orchestration dagster-webserver -h {{ host }} -p {{ dagster_port }} &
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
`publish/build_report.py` writes to, because [publish-and-swap](PUBLISH_AND_SWAP.md)'s `current` symlink is not
built. That is the one place the recipe is smaller than the design, and it costs
what the swap design says it costs: the site is *down* for the length of a rebuild, because
that module clears its output directory on every run, `--clean` or not.
**Measured** by polling two pages every 2 s through a `publish_site`
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
  Dagster 1.13.19 describes the command as starting "a **local**
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
  and [Standing it up](#standing-it-up) makes it a step.
- **`--group orchestration` on the Dagster processes; on the file server it is
  harmless.** `uv run` only ever *adds* what its groups need and never removes a
  package (measured on uv 0.12.12: a bare `uv run` left Dagster
  installed). What does strip the venv under a running service is a bare
  `uv sync`: `default-groups` is deliberately unset in `pyproject.toml`, so it
  syncs to `dev` alone, and `uv sync --dry-run` on this tree would uninstall 46
  packages, `dagster`, `dagster-webserver` and `grpcio` among them. The
  already-running webserver and daemon would survive on imports they hold in
  memory while everything they *fork later* dies — the grpc code servers, and the
  run worker forked per schedule tick. The ports answer, `wait -n` never returns,
  and nothing materialises: this section's own failure mode, arriving through
  the dependency resolver. [The invariants](#invariants-that-fail-silently-in-a-service) record it.
- **The port collision.** `just dagster` uses 3000 and so does `evidence dev`.
  The site here is static, so it is served by anything; give it its own port and
  do not reach for `evidence dev`, which is a hot-reloading dev server.

### Stopping it — measured

The tree is bigger than the recipe starts:
**twelve processes, not three**, because the webserver and the daemon each spawn
a `dagster api grpc` code server, which spawns a multiprocessing resource tracker
of its own. Whether the cleanup reaches all of that is a question rather than a
formality, so it was run — five ways of stopping it, against the real graph:

<details>
<summary>The five ways of stopping it, and the three rules</summary>

| stopped by | processes | left behind | `just` exits |
|---|---|---|---|
| Ctrl-C (SIGINT to the process group) | 12 | **0** | 130 |
| `systemctl stop` (SIGTERM to the group) | 12 | **0** | 143 |
| SIGTERM to `just` alone | 12 | **0** | 143 |
| SIGTERM to the recipe's shell alone | 12 | **0** | 0 |
| **one child killed** (`kill -9` on the site server) | 12 | **0** | **1** |

Three rules came out of that, and the first one matters most:

- **`kill` the recorded PIDs, not `kill 0`.** Measured separately: `uv run`
  forwards SIGTERM to the process it spawned, and the cascade carries on down to
  the grpc servers, so three PIDs are enough to stop twelve processes. `kill 0`
  signals the whole group *including the recipe's own shell*, which would then
  die **by SIGTERM** — and `man systemd.service`, read on the systemd this was
  measured against (259), lists SIGHUP, SIGINT, SIGTERM and SIGPIPE as
  *successful* termination alongside exit 0. So `kill 0` would quietly disable the
  `Restart=on-failure` written four paragraphs below: the unit would exit
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

</details>

### Reading its log — measured

Three log lines look like faults and are not; one of them is the line to act on.

<details>
<summary>The three lines</summary>

`just serve`'s output interleaves the three processes with every run's own
output, dbt's and Evidence's included. Watched through a scheduled
`full_refresh`, one launched from the UI, `load_retail` and `publish_site` on
Dagster 1.13.22, it carries three lines that look like faults:

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
  whether or not anything is queued. It is the one-run limit ([Missed ticks](#missed-ticks-and-one-run-at-a-time--measured)) being checked, not
  a run being refused.
- **dlt's `UserWarning: XDG_DATA_HOME is set to … but ~/.dlt already exists.
  Using ~/.dlt`**, at every code load and ingest. On a laptop it is noise. **On a
  service host it is the line to act on:** it says the runbook's `XDG_DATA_HOME` is being
  ignored because the service user has a `~/.dlt`, so the watermark lives there
  rather than on the volume step 2 named ([The state](#the-state-that-must-outlive-a-restart)).

`publish_site` adds a fourth from Evidence, `Column "last_revised_at" … contains
only null values so it has been cast to Float64`, which means no snapshot has
recorded a revision yet (`building-evidence-reports`).

</details>

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
EnvironmentFile=/srv/mds/service.env   # the state paths below — and nothing secret; see Exposure
ExecStart=/usr/bin/just serve
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

`[Install]` is what `systemctl enable` needs; without it the unit starts by hand
and never at boot. `Type=exec` rather than `simple` so a failure to execute
`just` is reported at start time instead of appearing to succeed.

## Standing it up

Two routes to the same service: the compose stack, which is the recommended
one, and a host runbook for running it without containers. Both are ordered,
because the two facts in [The schedule](#the-schedule-bootstrap-is-not-steady-state)
are only dangerous out of order.

### The compose variant

The same service as four containers, and the **recommended** way to run it:
steps 1 to 4 of the host runbook below collapse into two commands, because the
image is the host setup and `compose.yaml` is the environment file.

```sh
cp .env.example .env         # set PGPASSWORD; the rest has working defaults
just compose-build           # the image, mds:local
just compose-up              # postgres, seaweedfs, dagster, site
```

`127.0.0.1:3000` is Dagster and `:8081` is the dashboard. Then, once:

```sh
just compose-launch load_retail
just compose-launch full_refresh
just compose-launch publish_site
docker compose exec dagster uv run dagster schedule start daily_refresh
```

That is step 3's bootstrap and step 6, in the container. `compose-launch` is
`docker compose exec -T dagster uv run dagster job launch -j <job>`. It returns
once the run is queued, and the queue does not wait for the run ahead to
*succeed*, so check `load_retail` in the UI before trusting `full_refresh`.
**Never pass `-m`** — [the invariants](#invariants-that-fail-silently-in-a-service) say why.

**Until the last line runs, the stack is up and ingests nothing.** `just
compose-up` reports four running services and the daemon is running, but
`daily_refresh` ships `STOPPED` ([The schedule](#the-schedule-bootstrap-is-not-steady-state)) and its on/off state is a row in the
`dagster` database, so nothing in the image or `compose.yaml` can turn it on.
Nothing warns, either: the one sign is an empty `instigators` table, or `dagster
schedule list` inside the container printing `[STOPPED]`. A `compose-down
volumes` empties that database, so the reset needs this line again, like step 5
after a wiped `DAGSTER_HOME`. `just materialize` from the *host* is still not
the service's queue: it is a different process against a different warehouse
entirely, since the container's lives on the `mds_data` volume.

What differs from a host deployment, beyond packaging:

- **`restart: unless-stopped` is the supervisor.** It does what the systemd unit's
  `Restart=on-failure` and `WantedBy` do: a service whose `just serve` exits 1
  comes back, and all four return when the Docker daemon starts after a reboot
  — provided the daemon itself is enabled at boot. `docker compose stop` still
  stops them for good. It reaches only the four services; a run's container is
  the launcher's, not compose's, and is removed when it exits. The logging the
  unit supplied is `docker compose logs`.
- **Each run gets its own container**, launched from the same image, and
  removed when it finishes. Measured: about 10 s from launch to a
  running run container, against a subprocess starting immediately.
- **"The same image" means the service's image ID, not `mds:local`.** Stock
  `DockerRunLauncher` launches by name, and `just compose-build` moves the tag
  at once while the service keeps its old image until `just compose-up`
  recreates it, so a run launched in between would execute code the service is
  not running ([decision 0006](decisions/0006-runs-launch-from-the-service-image-id.md)).
  `modern_data_stack.docker_launcher` asks Docker for the
  launching container's own image instead. The daemon launches every queued
  run, so a build changes nothing until the service is recreated, and
  `compose-build` then `compose-up` is the deploy. It finds itself by hostname,
  so the `dagster` service must not set `hostname:`, which the instance test
  asserts.
- **Every start needs the dbt hub, though the image does not.** `serve`
  depends on `dbt-parse`, which depends on `dbt-deps`, so the container runs
  `dbt deps` on each start although `dbt_packages/` and the manifest are baked
  in. Measured with `--network none`: the image's own `CMD` exits in about
  8 s with `Failed to resolve 'hub.getdbt.com'`, in `dbt-deps`, before either
  port opens; with a network the same step takes about 3 s. `restart:
  unless-stopped` then restarts it, and a hub outage becomes a service that
  cannot come back up, where the image would have been fine. The dependency is
  the price of [decision 0005](decisions/0005-just-serve-first-container-second.md)'s
  one recipe for a laptop and the container, and it is left as it is; the
  fix would be a second path through `serve`, which is what 0005 rejects.
- **The landing zone is not on a volume at all.** The catalog is in Postgres and
  the Parquet in SeaweedFS, so the one file a run container and the service both
  open is `data/warehouse.duckdb` on `mds_data` ([`just serve`](#just-serve-and-the-container-built-on-it), fact 3).
- **Four named volumes**, and they are named explicitly because the run
  containers mount them by name from outside compose: `mds_data` (the warehouse
  and the landing-zone directory), `mds_dlt` (dlt's watermarks, at
  `DLT_DATA_DIR` rather than under `$HOME`), `mds_site` (what nginx serves), and
  `mds_dagster` (compute logs and run artifacts). Two more back the services:
  `mds_postgres` and `mds_seaweedfs`.
- **`just compose-down volumes` is the full reset**, and the only way to make
  `deploy/postgres/init.sql` run again. It destroys the catalog, the bucket, the
  warehouse and every run this instance recorded.

Verified end to end:

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

And in CI, on its first run, the `container` job — cold image build,
services up, `just test-pipeline` inside it — took **2 m 14 s** end to end on a
`ubuntu-latest` runner. No layer caching was added, because the whole job is
faster than the threshold that would have justified one.

**Still unmeasured** for the compose stack: a
`publish_site` *run container* writing the volume (the copy was measured from
the service container instead), the cold-cache build time on a CI runner, and
any of this on a host that is not this laptop.

### On a host

Steps 1, 3, 5 and 6 are things you can type; step 2's environment file and step
4's unit are still to be written, and the whole of it assumes the
[swap](PUBLISH_AND_SWAP.md) asset has not been built — which is the smaller
starting point the schedule section describes, not a blocker.

**1. The host, once.** Install `just` (`uv tool install rust-just`), clone, then
`just setup`, which syncs the venv and fetches the DuckDB extensions (`ducklake`,
`httpfs`, `postgres`), the dependencies no lockfile can name. **Node is required**, because
`just serve` serves the Evidence site and refuses to start without a built one —
the graph itself still does not touch it, so this is a cost of serving rather
than of running.

**2. The paths, once.** Write `/srv/mds/service.env` with the [state](#the-state-that-must-outlive-a-restart) set, all
absolute, and nothing secret in it:

```sh
PROJECT_ROOT=/srv/mds/repo
LAKEHOUSE_DIR=/srv/mds/state/lakehouse
INGEST_CACHE_DIR=/srv/mds/state/cache
DAGSTER_HOME=/srv/mds/state/dagster
XDG_DATA_HOME=/srv/mds/state          # or dlt's watermark follows $HOME
WAREHOUSE_PATH=/srv/mds/state/build/warehouse.duckdb   # the *build* file (publish-and-swap)
SITE_ROOT=/srv/mds/state/sites/current
```

`WAREHOUSE_PATH` is the one line that encodes the [publish-and-swap](PUBLISH_AND_SWAP.md) decision. Point it at the
served file instead and the graph builds in place, which is the smaller starting
point the schedule section describes.

`DAGSTER_HOME` on the volume starts without the checked-in instance config, and
with it the one-run limit ([Invariants](#invariants-that-fail-silently-in-a-service)). Link it rather than copy it, so it follows the
repo:

```sh
mkdir -p /srv/mds/state/dagster
ln -s /srv/mds/repo/.dagster/dagster.yaml /srv/mds/state/dagster/dagster.yaml
```

**Not `deploy/`**, though it is the tidier-looking option and this section
once offered it. That file is the *container's* instance: its `run_launcher`
resolves each run's image by asking the Docker daemon what the **launching
container** was created from, and nothing in Dagster checks whether it is
running in one. A host pointed there starts cleanly and then fails the first
time the daemon dequeues a run, with `no container '<hostname>' on this Docker
daemon`. The failure is invisible to `just materialize` and the other
in-process recipes, which run under either instance, so it shows only on a UI
click, a schedule tick or a backfill. The measurement, and the split-config
alternative weighed against it, are
[decision 0011](decisions/0011-deploy-is-the-containers-instance.md).

`$DAGSTER_STORAGE_DIR` needs nothing here either: only `deploy/dagster.yaml`
reads it ([The state](#the-state-that-must-outlive-a-restart)), and the linked instance above keeps compute logs and run
artifacts in `$DAGSTER_HOME/storage/`, which is already on the volume.

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
minus restart, boot and logging; the systemd unit is what supplies those and is the
part still to be written. Install it, then `systemctl enable --now mds`. Either
way, confirm the code location actually loaded, and do not skip this on the
grounds that the port answers:

```sh
uv run dagster definitions validate -m orchestration.definitions
```

**A broken code location does not take the webserver down.** Measured (see [`just serve`](#just-serve-and-the-container-built-on-it)): with
`dbt/target/manifest.json` missing, the webserver still answers HTTP and the
daemon still runs, while the only thing in the deployment that does any work
fails to load. A liveness probe on the port reports a healthy service that
cannot materialise anything. `definitions validate` is what distinguishes them,
and it names the cause: `DagsterDbtManifestNotFoundError`.

**5. Turn the schedule on. It is off until you do**, and this is instance state
in `DAGSTER_HOME`, not code, so it is also the step to repeat if that directory
is ever wiped:

```sh
uv run dagster schedule start daily_refresh    # never with -m
```

The UI toggle is the same action against the same instance; use either. What is
*not* equivalent is editing `default_status` in `definitions.py`, which changes
what `dagster dev` does for everyone who clones the repo.

**6. Refresh the site, and know that the schedule will not.** `daily_refresh`
targets `full_refresh`, which excludes `reports/evidence_site` ([The schedule](#the-schedule-bootstrap-is-not-steady-state)) — so the
warehouse moves daily and the served dashboard does not. Until the [swap](PUBLISH_AND_SWAP.md) asset
exists, the site is rebuilt by hand:

```sh
systemctl stop mds     # or Ctrl-C the foreground `just serve`
just report
systemctl start mds
```

**Stopping first is not caution, it is required.** `evidence sources` opens the
warehouse to extract its Parquet, which is a reader against a file a scheduled
build may be writing: one writer XOR many readers, across processes. `just
report` is not a Dagster run, so the queue cannot hold it back. Only the swap avoids
it.

**The route that needs no stop is `publish_site` from the UI.** It enters the
one-run queue, so it waits for a scheduled run instead of reading beside it, and
its Evidence step reads the warehouse only after its own build has finished. It
costs a second full ingest and build — 174 s, against 91 s for
`full_refresh` alone — and the 85 s of 404s measured under [`just serve`](#just-serve-and-the-container-built-on-it).

**7. Verify, and prefer the checks that fail loudly.** `dagster schedule list`
shows it RUNNING, and `dagster instance info` run with the service's
`DAGSTER_HOME` prints a `concurrency:` block with `max_concurrent_runs: 1`; no
block at all means step 2's link is missing. After the first scheduled run, the
useful assertions are the ones the repo already computes rather than a glance at
the dashboard:
`analytics.pipeline_sources` for per-source load times and row counts,
`analytics.pipeline_tests` for anything failing, and the freshness policies in
the UI, which are the reason [What it replaces](#what-it-replaces) calls the daemon the thing that makes the SLA
real.

**What can go wrong quietly, in the order it bites:** step 6 is forgotten and
the dashboard ages against a warehouse that does not, with nothing red anywhere;
a wiped `DAGSTER_HOME` leaves the schedule stopped and the service serving an
ageing site for the other reason; a stray bare `uv sync` strips Dagster out of
the venv while it is running; a `$HOME` change moves dlt's watermark and
re-fetches everything; a moved mount point breaks every
DuckLake attach because `data_path` is compared as a string; a `DAGSTER_HOME`
without `dagster.yaml` lets runs overlap again. All six are [invariants](#invariants-that-fail-silently-in-a-service), and none of them
raises where you are looking.

## The state that must outlive a restart

Everything below has to be on durable storage, and each row fails differently:

| Path | Why it is state | What losing it costs |
|------|-----------------|----------------------|
| `data/lakehouse/` | dlt's landing zone, and the only copy of every raw table | the weather archive cold-starts at three years: days of Open-Meteo budget, gone silently |
| `data/warehouse.duckdb` | the `history` schema only; every other schema is derived | the revision log, permanently. No rebuild invents a version upstream has overwritten |
| dlt's data dir | the WDI watermark and the ECB's last fixing | a silent full re-fetch, or a five-year window into a warehouse with no history |
| `.dagster/` | the laptop instance: run and event storage (SQLite), plus **schedule on/off state**; its `dagster.yaml` is config, checked in, and has to be carried to any other `DAGSTER_HOME` | run history, and a service that looks running and ingests nothing ([The schedule](#the-schedule-bootstrap-is-not-steady-state)); without `dagster.yaml`, runs no longer queue behind each other ([The schedule](#the-schedule-bootstrap-is-not-steady-state)) |
| the `dagster` database | the same three, for the container stack: `deploy/dagster.yaml` puts run, event and schedule storage in Postgres, so they outlive a container that is replaced rather than restarted. Its first use created 22 tables, and after one `load_retail` the database was 9.3 MB | the same three losses, with nothing left on a filesystem to restore them from |
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

## The schedule: bootstrap is not steady state

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
  no schedule state at all — measured: starting the schedule against
  that instance wrote a `RUNNING` row to `instigators` and left every file under
  `.dagster/` byte-identical. The failure mode is unchanged, only relocated: drop
  the database and the service comes back up ingesting nothing.
- **It targets `full_refresh` only, which excludes two things.** It excludes
  `load_retail`: correct forever on an established lakehouse (retail is a closed
  archive whose partitions are replayed by hand), and a failure on a fresh one,
  inside `stg_retail_lines`, with `Catalog Error: Table with name
  retail_invoice_lines does not exist!`. **It also excludes
  `reports/evidence_site`.** Under `just serve`, only `publish_site` builds the
  site and *nothing schedules `publish_site`*, so a scheduled service keeps the
  warehouse current and leaves the dashboard exactly where the last `just
  report` left it — no error, no log line, a page that simply stops moving. The
  exclusion is deliberate and stays (three workflows run `full_refresh` on a
  bare uv checkout with no Node), so the answer is not to move the asset into
  the job but to refresh the site alongside the graph: step 6 of the host runbook by hand,
  and the [swap](PUBLISH_AND_SWAP.md) properly.

So **the service's first run is a different command from its steady state** —
[Standing it up](#standing-it-up) is the runbook that says so, rather than leaving it to be discovered on a
rebuilt host at 06:00 UTC.

**`daily_refresh` is the only scheduler**, and adopting the [swap](PUBLISH_AND_SWAP.md) does not change that.
The swap is an asset inside `full_refresh`, so the schedule that runs the graph
runs the swap with it; there is no systemd timer and no second cron. What
adopting it changes is one environment variable (`WAREHOUSE_PATH` points at the
build file rather than the served one) and one asset on the end of the graph.

**Without the swap the schedule still works**, materialising into the served warehouse
in place. That is the smaller starting point and it costs three things. Two are
not silent: a reader lockout for the length of a build ([Invariants](#invariants-that-fail-silently-in-a-service)), and a half-written
file where the good one was if the build goes red. **The third is silent** — the
site is served from a fixed `reports/build/` that only a manual `just report`
rewrites, so the dashboard ages while the warehouse behind it does not. All
three are survivable on an internal deployment; only the third needs somebody to
remember.

### Missed ticks, and one run at a time — measured

**A daemon that starts after a missed tick launches it at once.** Measured
with `daily_refresh` `RUNNING` in this instance and the service down
across more than one 06:00 UTC tick: `just serve` started at 11:52 UTC, and
within 16 s the daemon logged `daily_refresh has no partition set, so not trying
to catch up` and launched a `full_refresh` for that morning's tick. Only the
latest missed tick runs — `dagster/_scheduler/scheduler.py` drops the rest for a
schedule with no partition set. A restart eighteen minutes later launched
nothing, because that tick was already recorded. So a host rebooted, or
restarted by systemd after a crash, any time after 06:00 UTC starts a full build
while whoever restarted it is opening the UI, and without the limit below a
Materialize click in that window would be a second writer against a file DuckDB
lets one process write ([Invariants](#invariants-that-fail-silently-in-a-service)).

<details>
<summary>How this was measured, and what the queue does and does not hold</summary>

**`.dagster/dagster.yaml` holds the instance to one run in progress**
(`concurrency: runs: max_concurrent_runs: 1`; Dagster's default is 10). Measured
against a throwaway instance whose only job sleeps, so no warehouse was involved:
under Dagster's default two launches were both `STARTED` within 5 s, and under
this file the second stayed `QUEUED` until the first finished. The file is read
at process start, so a change to it takes a restart.

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
has not loaded the runbook's environment file is invisible to the service's queue in both
directions.

</details>

## Exposure

**Dagster's webserver has no authentication.** Bind it to localhost and put
whatever the host already terminates TLS with in front of it. The Evidence site
is static and safe to expose; note that it ships the underlying Parquet to the
browser, so "the site is public" means "these tables are public".

**On its own the service holds no secrets.** Every source it reads is public
and unauthenticated, its environment file is paths, and `PII_SALT`, the one
secret in the export, belongs to a step the service does not run ([publish-and-swap](PUBLISH_AND_SWAP.md)). If
publishing is ever added to the host, that stops being true immediately: the
salt has to be **stable across runs** (a fresh one repseudonymises every
customer for no change in the data), so it would become a long-lived secret
sitting on a machine that also serves traffic. That is the trade to weigh, and
it is the reason releases are left on GitHub here.
[`docs/DATA_PROTECTION.md`](./DATA_PROTECTION.md) has the reasoning.

**The compose stack brings secrets, and something worse than a secret.** In
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
  exposing a docker socket by proxy, so the authentication this section opens with stops
  being optional.
- **The container runs as root**, which the socket makes close to moot: a
  non-root user in the container that can write the socket has the host anyway.
  Recorded rather than fixed, so the trade is visible.
- **`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`** are credentials in the same
  sense, though the ones in `.env.example` reach a SeaweedFS bound to localhost.
  A real object store makes them the third real secret.

**The pseudonymisation happens at the export and nowhere else**, so the
warehouse the service builds and serves from holds `customer_id` in the clear,
exactly as a local build does. The *site* is fine: the retail source queries
only aggregate over that column (`count(distinct …)`, `… is null`), so no
Parquet reaching a browser carries an identifier. The exposure is therefore the
**file**, not the pages: do not serve `data/warehouse.duckdb` itself, and treat
a shell on the host as access to the personal column. A deployment that wants to
hand the database out needs the export path, and with it the salt.

## Invariants that fail silently in a service

The list [`docs/REUSING_THIS_STACK.md`](./REUSING_THIS_STACK.md#4-invariants-that-fail-silently)
keeps, extended with the ones only an always-on deployment meets:

- **dlt's data dir moves with `$HOME`.** A different service user resets the
  watermark with no error ([The state](#the-state-that-must-outlive-a-restart)).
- **`prepare_if_dev()` does not fire outside `dagster dev`.** The code location
  fails to load, or loads against a stale manifest ([`just serve`](#just-serve-and-the-container-built-on-it)).
- **A `STOPPED` schedule survives a `.dagster/` wipe as stopped.** The service
  runs, serves an increasingly old site, and ingests nothing ([The schedule](#the-schedule-bootstrap-is-not-steady-state)).
- **A run container's command is `dagster api execute_run`, not a `just`
  recipe.** Everything else in the image reaches the venv through `uv run`, so
  nothing in the justfile needs `/app/.venv/bin` on `PATH` — and the launcher
  does. Without it a run dies before any Python runs, with
  `exec: "dagster": executable file not found in $PATH`; `auto_remove` then
  deletes the container, so the only evidence is one `ENGINE_EVENT` in the event
  log. Measured, and the reason the Dockerfile's `PATH` is what it is.
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
  The outage is the copy. Measured in the compose stack: `just report`
  in the container built 11 pages / 482 files / 92 MB and copied them to the
  volume, and **65 one-second polls of nginx through the whole build returned 200
  every time** — the copy window did not last a second. Against the 85 s of 404s
  the in-place build produced ([`just serve`](#just-serve-and-the-container-built-on-it)), that is the whole point of the indirection.
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
  `[tool.dagster]` names it `modern_data_stack`. Measured against the
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
- **A `DAGSTER_HOME` outside the checkout has no `dagster.yaml`.** The host runbook puts it on
  the durable volume, where Dagster finds no config, prints one notice at start
  and falls back to its defaults — ten runs at once rather than one, so a restart
  after a missed tick plus one click is two writers again ([The schedule](#the-schedule-bootstrap-is-not-steady-state)). Measured: an
  empty `DAGSTER_HOME` reports `max_concurrent_runs` as 10, and one holding a
  symlink to the checked-in file reports 1.
- **`dagster instance info` prints `compute_logs: NoneType` for a configured
  compute log manager.** Measured on `deploy/dagster.yaml`: the line says
  `NoneType` while the instance's manager really is a `LocalComputeLogManager`
  writing to `$DAGSTER_STORAGE_DIR`. It is a display quirk of that command and
  not a config that failed to load — read it back off the instance rather than
  out of `instance info` before changing anything to chase it.
- **The recipes do not queue behind the service.** `just materialize` and
  `materialize-site` run `dagster job execute`, and `materialize-select` and both
  `backfill-*` recipes run `dagster asset materialize`; each executes in its own
  process, beside whatever the service is running, and only the other direction
  waits ([The schedule](#the-schedule-bootstrap-is-not-steady-state)).
- **A bare `uv sync` uninstalls Dagster.** `default-groups` is unset, so
  `uv sync` without `--group orchestration`, typed against a *running* service,
  strips 46 packages out of the venv under it. The running processes hold their
  imports and keep answering; the grpc code servers and run workers they fork
  afterwards do not exist any more. `uv run` does not do this — it only adds
  packages — so the recipes are safe; stop the service before a `uv sync`, or
  give it its own checkout ([`just serve`](#just-serve-and-the-container-built-on-it)). The `deploy` group is the same trap one group
  further out: against a venv built by `just deploy-deps`, a
  `uv sync --group dev --group orchestration` would uninstall `dagster-postgres`
  and `psycopg2-binary` (measured with `--dry-run`), and the next restart
  of a `deploy/` instance fails on its own storage config.
- **The schedule refreshes the warehouse and never the site.** `daily_refresh`
  targets `full_refresh`, which excludes `reports/evidence_site`; only
  `publish_site` builds it and nothing schedules that. So a host that follows [the runbook](#standing-it-up)
  serves a dashboard frozen at bootstrap over a warehouse that updates daily,
  indefinitely, with no error anywhere ([The schedule](#the-schedule-bootstrap-is-not-steady-state)).
- **`LAKEHOUSE_DIR` must keep one absolute spelling for the deployment's whole
  life.** DuckLake compares `data_path` as a *string*, so moving the volume's
  mount point refuses every attach, and the error surfaces inside `dbt build`,
  one layer below whatever chose the spelling.
  - **A catalog remembers the data path it was created with, so one catalog
    schema cannot serve two arrangements.** Hit for real: the compose stack was
    pointed at a Postgres catalog that an earlier laptop run had already
    initialised with the Parquet *on disk*, and the first run in a container
    failed with `DATA_PATH parameter "s3://lake/modern-data-stack/" does not
    match existing data path in the catalog "/home/…/data/lakehouse/data/"`.
    Nothing was wrong with either side. A laptop that shares a database with the
    compose stack wants its own `LAKEHOUSE_METADATA_SCHEMA`, or the same data
    path as the stack.
- **DuckDB is one writer XOR many readers, across processes.** Measured on the
  pinned 1.5.5, in both directions: a read-only connection fails while another
  process holds the file read-write, and a writer fails while a read-only
  connection is open. So a forgotten interactive session blocks the next
  scheduled build, and the build blocks every reader, which is the strongest
  argument for the [swap](PUBLISH_AND_SWAP.md), where the served warehouse is never written to at all.
