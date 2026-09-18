# The whole stack in one image: the Dagster webserver and daemon, every layer
# they call, and the Node toolchain the Evidence site needs.
#
# `CMD` is `just serve` — the same recipe a host would run. That is deliberate:
# `docs/RUNNING_AS_A_SERVICE.md` §2's first reason against a container was that
# an image restates the service, so it drifts from the recipe. It restates the
# *toolchain* instead, and the recipe stays the one definition of what running
# means.
#
# Every tag is exact. Dependabot's `docker` ecosystem watches Dockerfiles, which
# is why `.github/dependabot.yml` gained it alongside `docker-compose`, and
# `tests/test_dagster_instance.py` refuses a `latest` or a bare name.

FROM node:24.21.0-bookworm-slim AS node
FROM ghcr.io/astral-sh/uv:0.12.16 AS uv

FROM python:3.13.15-slim-bookworm

COPY --from=uv /uv /uvx /usr/local/bin/

# Node from the node image rather than from Debian, so the site is built with
# the major `pages.yml` builds it with (24). npm and npx are the symlinks that
# image has, recreated here because `COPY` does not follow them out.
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# UV_NO_SYNC because every recipe runs `uv run --group orchestration …`, which
# would otherwise re-resolve and re-install at container start — over the
# network, inside a service that is supposed to be already built. The venv is
# built once, below. UV_PYTHON_DOWNLOADS=never keeps uv on the image's
# interpreter rather than fetching its own.
#
# PROJECT_ROOT because `modern_data_stack.paths` raises rather than guessing,
# and DAGSTER_HOME because `deploy/dagster.yaml` is the instance this image runs
# (storage in Postgres; see docs/RUNNING_AS_A_SERVICE.md §3).
#
# **`/app/.venv/bin` on PATH is load-bearing, and not a convenience.** Every
# recipe reaches the venv through `uv run`, so nothing in the justfile needs it
# — but `DockerRunLauncher` does not run a recipe. It starts each run container
# with `dagster api execute_run …` as the command, and without the venv on PATH
# that fails before any Python runs, with
# `exec: "dagster": executable file not found in $PATH`. The run is marked
# FAILURE from the daemon, `auto_remove` deletes the container, and the only
# evidence is in the event log.
ENV UV_NO_SYNC=1 \
    UV_FROZEN=1 \
    UV_PYTHON_DOWNLOADS=never \
    PROJECT_ROOT=/app \
    DAGSTER_HOME=/app/deploy \
    PATH=/app/.venv/bin:$PATH

WORKDIR /app

# The dependency layers first, so editing a model or a page does not re-resolve
# Python or re-download node_modules. `src/` is here rather than in the `COPY . .`
# below because the project is an editable install of it: uv builds the package
# during this sync, and a later copy of the same files does not invalidate it.
#
# `README.md` is in this layer for the same reason and not as documentation:
# `[project] readme` names it, so the build backend opens it and the sync fails
# with `failed to open file /app/README.md` without it.
COPY pyproject.toml uv.lock .python-version README.md ./
COPY src/ ./src/
# That includes `just`, the interface to every layer: the `deploy` group
# carries it, so `uv.lock` pins it like everything else and `/app/.venv/bin`
# on PATH finds it. A `uv tool install` here would take whatever was newest on
# each build, which is the one version in the image nothing watched.
RUN uv sync --frozen --no-dev --group orchestration --group deploy

COPY reports/package.json reports/package-lock.json ./reports/
# `npm ci` and never `install`: it installs the lockfile exactly and never
# re-resolves, which is the only thing that works here — `reports/package.json`
# cannot be resolved from scratch (an ERESOLVE peer conflict; see the
# `dependency-versions` skill).
RUN npm ci --prefix reports

COPY . .

# The DuckDB extensions, which are binaries no lockfile can name. Baked in, so a
# run container never downloads one mid-`dbt build`.
RUN just extensions

# The dbt manifest, at *build* time, because `orchestration/assets.py` reads
# `dbt/target/manifest.json` at import and every run container imports
# `orchestration.definitions`. `dbt deps` runs with it, so `dbt_packages/` is
# baked too.
#
# The throwaway paths keep this parse off any real catalog: `dbt parse` attaches
# the lakehouse, and pointing it at the deployment's would either fail (nothing
# is running at build time) or create one. The manifest does not record them —
# the database name comes from the file's stem, which stays `warehouse`.
RUN LAKEHOUSE_DIR=/tmp/lh WAREHOUSE_PATH=/tmp/lh/warehouse.duckdb just dbt-parse \
    && rm -rf /tmp/lh

# Dagster on 3000 and the site on 8081, bound to every interface because the
# only thing that can reach them is the compose network — `compose.yaml`
# publishes 3000 on 127.0.0.1 and puts the site behind nginx.
CMD ["just", "serve", "3000", "8081", "0.0.0.0"]
