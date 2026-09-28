# Publish-and-swap

**Design, not built.** Nothing below exists in the repo; the measurements are
of the pieces it would rest on.

- **The service would build into a scratch warehouse and swap it in only if it
  passes**: `release-data.yml`'s shape with the publishing taken out
  ([The cycle](#the-cycle)).
- **Dagster triggers every step, and there is no second scheduler**: the swap
  is an asset at the end of the graph ([The cycle](#the-cycle)).
- **A rename over an open database is safe**: a held reader keeps the old
  database whole, and a new reader sees the new one
  ([The swap semantics](#the-swap-semantics--measured)).
- **An Evidence build can be pointed at the scratch file, with a trap**: an
  absolute path is silently made relative
  ([Pointing the site](#pointing-the-site-at-a-scratch-warehouse--measured)).
- **It removes the lock's nuisance, not its ceiling**
  ([What this does not solve](#what-this-does-not-solve)).
- **One thing is unmeasured: a full swap cycle, end to end**
  ([What is still unmeasured](#what-is-still-unmeasured)).

What runs today, and how to stand it up, is
[`RUNNING_AS_A_SERVICE.md`](RUNNING_AS_A_SERVICE.md).

## The cycle

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
  import**, and `orchestration/assets.py` binds its own the same way, so the code
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
session. That last row is the single-writer lock's nuisance ([invariants](RUNNING_AS_A_SERVICE.md#invariants-that-fail-silently-in-a-service)) genuinely dissolving rather than
merely being avoided.

**Re-running this needs separate processes.** DuckDB's Python client caches an
instance per path within a process, so asking it for a "new" connection to the
swapped path returned the *old* one, reporting `v1` after the swap, and then
refused a writer with `Can't open a connection to same database file with a
different configuration`. Both answers looked like filesystem findings and
neither touched the filesystem. Open the second connection in a subprocess.

Three properties make this worth the machinery:

- **The live warehouse becomes read-only by construction.** Nothing writes to it
  between swaps. The single-writer lock stops applying to every reader outside the
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

## What this does not solve

A service on one host changes none of the ceilings.
[`docs/FOR_REVIEWERS.md`](./FOR_REVIEWERS.md#4-what-breaks-at-1000) already
covers them: the Polars step's in-memory rank, the single-writer lock and the
`quack` route off it, full-refresh materialisation, and the point where shipping
Parquet to the browser stops working. Publish-and-swap removes the lock's
*operational* nuisance without raising its ceiling; one process still does every
write.

## What is still unmeasured

The standard this repo holds itself to is that a claim in the docs was measured.
One thing here was not, and it is the one that cannot be measured without
building the design first:

- **A full swap cycle end to end**, timed against the ≈65 s stage baseline in
  [`docs/FOR_REVIEWERS.md`](./FOR_REVIEWERS.md#3-what-does-a-run-cost-and-how-long-does-it-take).
  The swap adds a restore, a verify and two renames to a run whose largest stage
  is ingest, mostly network, so the expectation is that it disappears into the
  noise. But the restore copies `history` and the weather archive, which is real
  I/O, and nobody has timed it.
