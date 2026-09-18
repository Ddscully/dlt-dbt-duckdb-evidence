# 0008. Dependabot ignores Postgres major versions, which are migrations

Status: accepted 2026-09-18 (#79)

## Context

Dependabot's `docker-compose` entry watches `compose.yaml`, including the
`postgres` image that holds the DuckLake catalog and Dagster's storage. It opened
#73, bumping `postgres` 17.11 to 18.6. Postgres 18's official image changed where
it keeps its data, and the bump failed both ways:

- **As written, the stack did not start.** The 18 image refuses a volume mounted
  at `/var/lib/postgresql/data`, so the `container` job went red with
  `mds-postgres-1 is unhealthy`, and `dagster` waits on a healthy Postgres.
- **The obvious fix loses the data silently.** Mounting at `/var/lib/postgresql`
  starts an *empty* cluster in `18/docker/` beside the 17 files. The catalog and
  Dagster's run history come up blank, the health check passes, and the next
  ingest writes into a fresh catalog. CI starts from an empty volume, so it can
  only ever see the first failure.

## Decision

The `docker-compose` entry ignores `version-update:semver-major` for `postgres`
alone. Patches within the major, and every other image, still arrive. #73 was
closed.

## Rejected

- **Merging the bump with the mount changed.** It is the silent-data-loss path
  above, and CI cannot see it.

## Consequences

- A major is a deliberate migration PR: change the mount, then dump and restore,
  tested on a copy of a populated `mds_postgres` volume.
