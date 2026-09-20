# 0011. `deploy/` is the container's Dagster instance, and only the container's

Status: accepted 2026-09-20

## Context

There are two instance configs. `.dagster/dagster.yaml` is the laptop's: run,
event and schedule storage are SQLite files beside it, which is why this demo
needs no services. `deploy/dagster.yaml` puts those three in Postgres.

When `deploy/` was introduced, `docs/RUNNING_AS_A_SERVICE.md` §10 offered it to
a *host* as well, as the tidier of two ways to give a systemd unit a
`DAGSTER_HOME`: the config is already in the repo, so nothing is linked. The
alternative it was offered against is a symlink from the state volume to the
checked-in laptop file.

That was true when it was written. `deploy/dagster.yaml` then gained a
`run_launcher` block (0006), which resolves each run's image by asking the
Docker daemon what the **launching container** was created from. Nothing in
Dagster checks whether it is running inside a container, so the host variant
stayed in the doc, reading correctly, describing something that cannot work.

**Measured on a host configured exactly as §10 asked** — `PGPASSWORD` and the
four `DAGSTER_*` lines in `.env`, `just deploy-deps` run, a reachable Postgres:

- A host process on `DAGSTER_HOME=<repo>/deploy` does resolve
  `ServiceImageDockerRunLauncher`. Nothing refuses it at startup.
- The first thing the daemon does when it dequeues a run is ask for the image,
  and that raises this repo's own error:
  `no container '<hostname>' on this Docker daemon, so the run image cannot be
  the service's own.`
- Behind it, never reached, is a second failure. The launcher's `env_vars` is an
  unconditional copy list of eighteen names, of which eleven are unset on such a
  host. `PROJECT_ROOT`, `WAREHOUSE_PATH`, `DLT_DATA_DIR` and `INGEST_CACHE_DIR`
  are assigned only by `compose.yaml` and the Dockerfile, and their values are
  container paths; `LAKEHOUSE_CATALOG`, `LAKEHOUSE_DATA_PATH` and the `AWS_*`
  names are what a host on the default on-disk landing zone leaves unset on
  purpose. There is no host configuration that satisfies the list.
- `dagster_docker/docker_run_launcher.py` asks for the image inside `launch_run`
  and parses the copy list only in `_launch_container_with_command`, so the
  image error is the one an operator sees.

**The break is invisible to every command an operator is likely to try.**
`dagster job execute` and `asset materialize` — every `just materialize*` and
`backfill-*` recipe — run in the calling process under either instance. The
launcher governs only *queued* runs, so a host set up this way passes a manual
pipeline run and fails on the first UI click, schedule tick or backfill.

## Decision

`deploy/` is the container's instance. A host points `DAGSTER_HOME` at a
directory holding a symlink to `.dagster/dagster.yaml`, which is what §10 now
describes.

Postgres storage is kept, because its two reasons are both container reasons: a
container is *replaced* rather than restarted, and a run in one container must
be visible to a daemon in another. A host has neither problem — SQLite on the
state volume survives a restart.

`just deploy-deps` stays. Its job on a laptop is that
`tests/test_docker_launcher.py` `importorskip`s `dagster_docker` and otherwise
skips in silence, which is the wrong answer in CI and the right one on a clone.

## Rejected

- **Splitting the config: a third file (say `deploy/host/dagster.yaml`) with
  Postgres storage and no `run_launcher`.** It works, and it is the obvious fix.
  It costs a third copy of the blocks the two files already duplicate, because
  Dagster has no include directive, and `tests/test_dagster_instance.py` would
  have to hold three files in agreement rather than two. What it buys is one
  Postgres backup covering both the DuckLake catalog and Dagster's history, for
  an operator who wants a host process and a database but no containers. Nothing
  in the repo needs that shape, and the duplication is paid by everyone.
- **Making the launcher fall back to a tag outside a container.** That is
  exactly the drift 0006 exists to remove, and it would fail later and more
  quietly: runs would execute the wrong code rather than refuse to start.
- **Leaving the doc alone and letting the error teach.** The error is good — it
  says "The launcher must run in a container" — but it arrives on the first
  scheduled tick, after a bootstrap that appeared to work.

## Consequences

- The `DAGSTER_*` and `DAGSTER_STORAGE_DIR` assignments left `.env.example`:
  `compose.yaml` sets all four itself, and no person sets them now.
  `tests/test_dagster_instance.py` unions `.env.example`, the service's
  environment and the Dockerfile's `ENV`, so the guard still covers them.
- A host running the compose stack is unaffected; this is only about a host
  running `just serve` directly.
- **What would make this worth revisiting:** a deployment target that wants
  Dagster's history in Postgres without containers. The answer then is the
  rejected split above, and the measurement to redo first is whether the
  duplicated blocks can be generated rather than copied.
