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
policies actually evaluated. Not multi-tenancy, not a query API, not a cluster —
[`docs/FOR_REVIEWERS.md`](./FOR_REVIEWERS.md#4-what-breaks-at-1000) covers where
this shape stops scaling, and none of that changes.

## 1. What it replaces

| Workflow | Under a service |
|----------|-----------------|
| `ci.yml` | **stays.** It is about the repo, not the data — fixture-backed, offline, per PR. A service has nothing to say about a pull request. |
| `nightly.yml` | **redundant**, if the service runs live daily and alerts. Its job is to distinguish "we broke it" from "OWID is down", and a service that ingests live inherits exactly that signal. |
| `pages.yml` | **redundant** if the service serves the site. Keep it only if the public mirror is wanted for its own sake. |
| `release-data.yml` | **stays, and the service deliberately does not do it.** GitHub is the distribution channel; the service is not. Publishing is a monthly, outward-facing act with its own obligations — attribution, pseudonymisation, a storage-format ceiling — and none of them get easier by moving to a host that is also serving traffic. See §4 for what the service borrows from it and what it leaves behind. |

**The gain is not parity, it is that the SLA starts being enforced.** The
freshness policies in [`orchestration/assets.py`](../orchestration/assets.py) —
warn at two days without a load for `raw/*`, fail at seven; the modelled layers
rebuilt by 08:00 UTC — are declared today and **evaluated by nothing** between CI
runs. A schedule that quietly stopped firing is supposed to show as a stale asset
rather than as an absence somebody notices; that only happens with a daemon
running. See [`docs/FOR_REVIEWERS.md`](./FOR_REVIEWERS.md#2-what-is-the-freshness-sla-and-what-happens-when-it-is-missed).

## 2. `just serve`, not a container

**Recommendation: a `just serve` recipe, supervised by systemd. Reach for a
container only when the target demands one** (several hosts, immutable images) —
and then its `CMD` should be `just serve`, so it inherits the definition rather
than restating it.

Four reasons, all specific to this repo rather than to taste:

1. **The justfile is already the single definition of the environment.**
   [`.github/actions/setup`](../.github/actions/setup/action.yml) exists
   *because* four workflows each restated that environment and two restated it
   wrongly. A Dockerfile is a fifth restatement of the same facts — a base
   image, an OS package set, a Node install, an env block — and
   `tests/test_workflows.py`, which is what stops the other four drifting,
   cannot guard it, because it is not a workflow.
2. **A base image is a new pinning surface nothing watches.** Three versions
   here can only age deliberately (`.python-version`, the sqlfluff pair, ruff)
   because no Dependabot ecosystem covers them. A base image tag would be a
   fourth thing somebody has to remember, for no functional gain on one host.
3. **A container does not solve the constraint that actually binds.** The
   single-writer lock is a property of *the file plus a process*, not of the
   host. Two containers sharing a volume reintroduce it across a filesystem
   boundary — strictly worse than one process tree, where `in_process_executor`
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
    python -m http.server 8081 --directory "$SITE_ROOT" &
    wait
```

Three things in that block are load-bearing:

- **`dagster-webserver` and `dagster-daemon`, not `dagster dev`.** Dagster
  documents `dagster dev` as a local-development entry point; it runs both in
  one process tree with no supervision of either half.
- **`dbt-parse` as a dependency, not an afterthought.** `prepare_if_dev()` in
  [`orchestration/resources.py`](../orchestration/resources.py) fires only under
  the dev CLI, which sets `DAGSTER_IS_DEV_CLI`. Run the webserver directly and it
  does not fire, so `dbt deps && dbt parse` has to happen first or the code
  location will not load at all — `dbt/target/` is gitignored, so this bites on
  every fresh deploy. The justfile already records this at every headless recipe.
- **The port collision.** `just dagster` uses 3000 and so does `evidence dev`.
  The site here is static, so it is served by anything; give it its own port and
  do not reach for `evidence dev`, which is a hot-reloading dev server.

### The supervisor

`just serve` dies with the SSH session. That is a systemd unit's job, not a
recipe's, and the layering keeps the recipe as the single definition of *what*
runs while the unit supplies restart, boot and logging:

```ini
[Service]
Type=exec
User=mds
WorkingDirectory=/srv/mds/repo
EnvironmentFile=/srv/mds/service.env   # the paths in §3 — and nothing secret; see §6
ExecStart=/usr/bin/just serve
Restart=on-failure
```

## 3. The state that must outlive a restart

Everything below has to be on durable storage, and each row fails differently:

| Path | Why it is state | What losing it costs |
|------|-----------------|----------------------|
| `data/lakehouse/` | dlt's landing zone, and the only copy of every raw table | the weather archive cold-starts at three years — days of Open-Meteo budget, gone silently |
| `data/warehouse.duckdb` | the `history` schema only; every other schema is derived | the revision log, permanently. No rebuild invents a version upstream has overwritten |
| dlt's data dir | the WDI watermark and the ECB's last fixing | a silent full re-fetch, or a five-year window into a warehouse with no history |
| `.dagster/` | run and event storage (SQLite), plus **schedule on/off state** | run history, and a service that looks running and ingests nothing (§5) |
| `data/cache/` | the retail workbook | a download, never data |

**The dlt row is the one a service gets wrong**, and the reason is written into
`build_pipeline()` already: that directory is `~/.dlt/pipelines/<name>/` **if
`~/.dlt` already exists**, and `$XDG_DATA_HOME/dlt/pipelines/<name>/` otherwise.
It resolves from `$HOME`. Run the service under a system user whose home is not
the developer's and the watermark is simply not there — no error, no warning,
just a full re-fetch on the first run and a five-year window on the second.
`XDG_DATA_HOME` pointed at the durable volume is the lever, and it belongs in
the unit's environment file next to the other paths.

The rest are the environment variables the code already reads, all absolute:
`PROJECT_ROOT`, `WAREHOUSE_PATH`, `LAKEHOUSE_DIR`, `INGEST_CACHE_DIR`,
`DAGSTER_HOME`. `modern_data_stack.paths` is the one resolver behind all of them,
which is what makes a service configurable at all — see
[`docs/REUSING_THIS_STACK.md`](./REUSING_THIS_STACK.md#4-invariants-that-fail-silently).

## 4. Publish-and-swap

**The service's build cycle is `release-data.yml`'s shape with the publishing
taken out**, and every piece it keeps already exists in `publish/`. Build into a
scratch warehouse; swap it in only if it passes.

What it borrows is the **carry-forward**: `restore_history` and the "did not
shrink" check. Those are not publishing — they exist because `history` and
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
   `modern_data_stack.history.carried_rows` — the same rules the restore used,
   so a relation added to `CARRIED` reaches the check too.
4. **Swap.** `rename(2)` the scratch warehouse over the live one, then build the
   site and flip a `current` symlink at the served directory (`ln -sfn`, atomic
   via rename).

Three properties make this worth the machinery:

- **The live warehouse becomes read-only by construction.** Nothing writes to it
  between swaps. §8's lock problem stops applying to every reader outside the
  build — ad-hoc `just sql`, inspection, a future query API.
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
  Confirmed against the installed Evidence at both layers — `loadSourceConfig`
  merges the environment *over* the file, and a full `evidence sources` run
  against an empty scratch database failed on every table, which is the
  extraction genuinely reading somewhere else.
- **An absolute path is silently made relative.** The DuckDB connector does
  `path.join(sourceDirectory, filename)`, and `path.join` does not respect a
  leading slash — so `/srv/mds/scratch/warehouse.duckdb` was opened as
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

Two facts that combine into this repo's collected failure mode — looks running,
is not:

- **`daily_refresh` ships `STOPPED`**, deliberately: opening the UI should not
  start hammering public APIs on a timer. Starting it is **instance state in
  `.dagster/`**, not code, so it survives a restart only if that directory is on
  the durable volume of §3. Flipping `default_status` instead is a code change
  that changes what `dagster dev` does for everyone.
- **It targets `full_refresh` only**, which excludes `load_retail`. That is
  correct forever on an established lakehouse — retail is a closed archive whose
  partitions are replayed by hand — and it fails on a fresh one, inside
  `stg_retail_lines`, with `Catalog Error: Table with name retail_invoice_lines
  does not exist!`.

So **the service's first run is a different command from its steady state**, and
a deployment runbook has to say so rather than leave it to be discovered on a
rebuilt host at 06:00 UTC.

## 6. Exposure

**Dagster's webserver has no authentication.** Bind it to localhost and put
whatever the host already terminates TLS with in front of it. The Evidence site
is static and safe to expose; note that it ships the underlying Parquet to the
browser, so "the site is public" means "these tables are public".

**The service holds no secrets, and that is a consequence of §4 rather than a
happy accident.** Every source it reads is public and unauthenticated, and
`PII_SALT` — the one secret in the whole project — belongs to the export, which
the service does not run. Its environment file is paths. If publishing is ever
added to the host, that stops being true immediately: the salt has to be
**stable across runs** (a fresh one repseudonymises every customer for no change
in the data), so it would become a long-lived secret sitting on a machine that
also serves traffic. That is the trade to weigh, and it is the reason releases
are left on GitHub here. [`docs/DATA_PROTECTION.md`](./DATA_PROTECTION.md) has
the reasoning.

**The pseudonymisation happens at the export and nowhere else**, so the
warehouse the service builds and serves from holds `customer_id` in the clear,
exactly as a local build does. The *site* is fine — the retail source queries
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
*operational* nuisance without raising its ceiling — one process still does every
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
  mount point refuses every attach — and the error surfaces inside `dbt build`,
  one layer below whatever chose the spelling.
- **An Evidence `filename` override is joined onto the source directory.** An
  absolute path is not rejected, it is relocated (§4).
- **DuckDB is one writer XOR many readers, across processes.** Measured on the
  pinned 1.5.5, in both directions: a read-only connection fails while another
  process holds the file read-write, and a writer fails while a read-only
  connection is open. So a forgotten interactive session blocks the next
  scheduled build, and the build blocks every reader — which is the strongest
  argument for §4, where the served warehouse is never written to at all.

## 9. What is still unmeasured

The standard this repo holds itself to is that a number in the docs was measured.
Three things here were not, and should be before anyone trusts the design:

- **A full swap cycle end to end**, timed against the ≈94 s stage baseline in
  [`docs/FOR_REVIEWERS.md`](./FOR_REVIEWERS.md#3-what-does-a-run-cost-and-how-long-does-it-take).
  The swap adds a restore, a verify and two renames to a run that is 65% network.
- **The webserver and daemon as separate processes against this code location.**
  Everything here runs through `dagster dev` or `dagster job execute`; the split
  is what the `prepare_if_dev()` bullet predicts a problem for, and predicting is
  not measuring.
- **`rename(2)` over a warehouse a reader has open.** POSIX says the open file
  survives under the old inode, so a mid-swap reader should see a consistent old
  database rather than a torn new one — but that is the documented behaviour, not
  this warehouse's observed behaviour.
