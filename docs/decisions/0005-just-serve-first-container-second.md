# 0005. The service is `just serve`; the container stack runs the same recipe

Status: accepted 2026-09-18 (#76)

## Context

Running the warehouse as a service (`docs/RUNNING_AS_A_SERVICE.md`) needed a
definition of what runs: the Dagster webserver, the daemon and a static file
server for the site. The obvious choice is a container. Before any container
existed, the recommendation was a `just serve` recipe supervised by systemd,
reaching for a container only when the target demanded one, for four reasons:

1. **The justfile is already the single definition of the environment.**
   `.github/actions/setup` exists because four workflows each restated that
   environment and two restated it wrongly. A Dockerfile looked like a fifth
   restatement that `tests/test_workflows.py` could not guard, because it is not
   a workflow.
2. **A base image is a new pinning surface nothing watches**, next to the three
   versions that can only age deliberately (`.python-version`, the sqlfluff
   pair, ruff).
3. **A container does not solve the constraint that binds.** The single-writer
   lock belongs to the file and a process, not the host, so two containers
   sharing a volume reintroduce it across a filesystem boundary.
4. **The serving half needs no runtime.** The site is static HTML plus Parquet
   that the browser queries with DuckDB-WASM, and it never opens the warehouse.

## Decision

`just serve` is the one definition of the service. On a host, systemd supervises
it. The container stack (`Dockerfile`, `compose.yaml`, a `DockerRunLauncher`
giving each run its own container) was then built on it rather than beside it,
and each reason above was answered rather than overruled:

1. **Answered by the shape.** The image's `CMD` is
   `["just", "serve", "3000", "8081", "0.0.0.0"]`, so it restates the toolchain
   and never the service. `tests/test_dagster_instance.py` reads the Dockerfile
   and `compose.yaml` as data and holds them against `deploy/dagster.yaml`,
   covering what a workflow guard could not.
2. **Conceded, then watched.** Four images are a real pinning surface, so
   `.github/dependabot.yml` gained `docker` and `docker-compose` ecosystems, and
   the instance test refuses a tag Dependabot could not bump. The count of
   versions that age only deliberately is still three.
3. **Dissolved by moving the state, not by the container.** With the catalog in
   Postgres and the Parquet in a bucket, the only file a run container and the
   service both open is `data/warehouse.duckdb`, which the one-run queue already
   serialises. The reason was right about the mechanism.
4. **Holds as written.** nginx serves the site volume read-only and never opens
   the warehouse.

## Rejected

- **A container as the primary definition, with its own entrypoint.** It would
  be the fifth restatement of the environment reason 1 warned about, and it
  would drift from what `just serve` runs on a laptop.
- **`dagster dev` as the service.** Upstream describes it as a local deployment,
  without automatic daemon restart, which is exactly what the supervisor
  supplies. The service runs `dagster-webserver` and `dagster-daemon` separately.

## Consequences

- The laptop default is still the file and still `just`. Compose is the
  recommended way to stand the service up, and `just serve` runs inside it.
- Running the webserver outside `dagster dev` means `prepare_if_dev()` never
  fires, so `just serve` depends on `dbt-parse`.
