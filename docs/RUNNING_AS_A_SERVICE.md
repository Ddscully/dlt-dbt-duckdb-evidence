# Running this warehouse as a service

> **Nothing in this document is built.** There is no `just serve` recipe, no unit
> file and no container in this repo. The pipeline runs from `just` recipes on a
> laptop and from four GitHub workflows on cron, and that is the whole of what
> works today. This is a design: what an always-on deployment would take, which
> parts the existing code already decides, and the three things measured while
> writing it that changed the answer. Read it as a plan, not as instructions.

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

## 2. `just serve`, not a container

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
   already serialises every write.
4. **The serving half needs no runtime at all.** `evidence sources` extracts the
   warehouse tables to Parquet under `reports/.evidence/`, `evidence build`
   renders static HTML into `reports/build/`, and the browser queries that
   Parquet with DuckDB-WASM. **The served site never opens
   `data/warehouse.duckdb`.** Serving it is a static file server, and there is
   nothing there to containerise.

### The recipe this implies

Not in the justfile. Shown so the design is concrete:

```just
# NOT BUILT — see docs/RUNNING_AS_A_SERVICE.md
serve: where dbt-parse
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p "$DAGSTER_HOME"
    trap 'kill 0' EXIT           # or Ctrl-C orphans both children
    uv run --group orchestration dagster-webserver -h 127.0.0.1 -p 3000 &
    uv run --group orchestration dagster-daemon run &
    python -m http.server 8081 --directory "$SITE_ROOT" &   # the `current` symlink of §4
    wait
```

Three things in that block are load-bearing:

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
- **The port collision.** `just dagster` uses 3000 and so does `evidence dev`.
  The site here is static, so it is served by anything; give it its own port and
  do not reach for `evidence dev`, which is a hot-reloading dev server.

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
| `.dagster/` | run and event storage (SQLite), plus **schedule on/off state** | run history, and a service that looks running and ingests nothing (§5) |
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
  start hammering public APIs on a timer. Starting it is **instance state in
  `DAGSTER_HOME`**, not code, and that was measured rather than assumed. A fresh
  instance reports `daily_refresh [STOPPED]`, `dagster schedule start` flips it
  to `[RUNNING]`, a *separate process* pointed at the same `DAGSTER_HOME` reads
  that back, and a `schedules/` directory appears under it. Pointed at a
  different `DAGSTER_HOME` the same schedule is still `STOPPED`, which is the
  same fact from the other side. So it survives a restart only if that directory is on the durable volume
  of §3, and a wipe silently returns the service to ingesting nothing. Flipping
  `default_status` instead is a code change that changes what `dagster dev` does
  for everyone who clones the repo.
- **It targets `full_refresh` only**, which excludes `load_retail`. That is
  correct forever on an established lakehouse (retail is a closed archive whose
  partitions are replayed by hand), and it fails on a fresh one, inside
  `stg_retail_lines`, with `Catalog Error: Table with name retail_invoice_lines
  does not exist!`.

So **the service's first run is a different command from its steady state** —
§10 is the runbook that says so, rather than leaving it to be discovered on a
rebuilt host at 06:00 UTC.

**`daily_refresh` is the only scheduler**, and adopting §4 does not change that.
The swap is an asset inside `full_refresh`, so the schedule that runs the graph
runs the swap with it; there is no systemd timer and no second cron. What
adopting §4 changes is one environment variable (`WAREHOUSE_PATH` points at the
build file rather than the served one) and one asset on the end of the graph.

**Without §4 the schedule still works**, materialising into the served warehouse
in place. That is the smaller starting point and it costs exactly two things: a
reader lockout for the length of a build (§8), and a half-written file where the
good one was if the build goes red. Both are survivable on an internal
deployment and neither is silent.

## 6. Exposure

**Dagster's webserver has no authentication.** Bind it to localhost and put
whatever the host already terminates TLS with in front of it. The Evidence site
is static and safe to expose; note that it ships the underlying Parquet to the
browser, so "the site is public" means "these tables are public".

**The service holds no secrets, and that is a consequence of §4 rather than a
happy accident.** Every source it reads is public and unauthenticated, and
`PII_SALT`, the one secret in the whole project, belongs to the export, which
the service does not run. Its environment file is paths. If publishing is ever
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
- **`LAKEHOUSE_DIR` must keep one absolute spelling for the deployment's whole
  life.** DuckLake compares `data_path` as a *string*, so moving the volume's
  mount point refuses every attach, and the error surfaces inside `dbt build`,
  one layer below whatever chose the spelling.
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
facts are only dangerous out of order. And, again, none of it runs today: it
assumes the `just serve` recipe of §2 and the swap asset of §4 have been built.

**1. The host, once.** Clone, then `just setup`, which syncs the venv and
fetches the DuckLake extension, the one dependency no lockfile can name. Node is
needed only if the deployment serves the Evidence site; `full_refresh` does not
touch it. Install `just` itself (`uv tool install rust-just`).

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

**3. Bootstrap, by hand, before the service exists.** This is the step that
differs from steady state, and it differs because `daily_refresh` targets
`full_refresh`, which excludes `load_retail`:

```sh
just materialize     # load_retail, then full_refresh — in that order
```

Skip it and the first scheduled run dies inside `stg_retail_lines` with
`Catalog Error: Table with name retail_invoice_lines does not exist!`. Doing it
by hand also means the first failure is watched rather than discovered in a log.

**4. The service.** Install the §2 unit, then `systemctl enable --now mds`.
Then confirm the code location actually loaded, and do not skip this on the
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

**6. Verify, and prefer the checks that fail loudly.** `dagster schedule list`
shows it RUNNING. After the first scheduled run, the useful assertions are the
ones the repo already computes rather than a glance at the dashboard:
`analytics.pipeline_sources` for per-source load times and row counts,
`analytics.pipeline_tests` for anything failing, and the freshness policies in
the UI, which are the reason §1 calls the daemon the thing that makes the SLA
real.

**What can go wrong quietly, in the order it bites:** a wiped `DAGSTER_HOME`
leaves the schedule stopped and the service serving an ageing site; a `$HOME`
change moves dlt's watermark and re-fetches everything; a moved mount point
breaks every DuckLake attach because `data_path` is compared as a string. All
three are §8, and none of them raises where you are looking.
