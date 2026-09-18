# 0006. A run container starts from the service's image ID, not the `mds:local` tag

Status: accepted 2026-09-18 (#81)

## Context

The stock `DockerRunLauncher` launches each run from an image *name*: the code
location's `DAGSTER_CURRENT_IMAGE`, falling back to its `image` config.
`just compose-build` moves the `mds:local` tag at once, while the running
service keeps the image it was created from until `just compose-up` recreates
it. In between, a run executes code the service is not running. Seen
2026-09-18: the service on image `230076f0ab43`, the tag on `fc7ff551e654`, and
a scheduled run between them.

## Decision

`modern_data_stack.docker_launcher` subclasses the launcher and asks the Docker
daemon, at launch, which image the launching container was created from. Runs
are queued, so only the daemon launches them and the launching container is
always the service. A run and the code that launched it are the same image by
construction, and the deploy is `compose-build` followed by `compose-up`.

## Rejected

- **A startup wrapper that sets `DAGSTER_CURRENT_IMAGE` to the service's image.**
  It misses runs launched through `docker compose exec … dagster job launch`,
  because `exec` gets compose's environment and not the wrapper's.
- **Falling back to the tag when the lookup fails.** That brings the drift back,
  so the launcher raises instead.

## Consequences

- The launcher finds its own container by hostname, which Docker sets to the
  container ID, so the `dagster` service must not set `hostname:`.
  `tests/test_dagster_instance.py` asserts it.
- It overrides `_get_docker_image`, a private method and the one place both
  `launch_run` and `resume_run` choose an image. `tests/test_docker_launcher.py`
  reads the stock source and fails if either stops calling it, because a Dagster
  upgrade that renamed it would silently bring back launching by tag.
