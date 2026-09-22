# The dev container: what a laptop would have after the README's Quickstart —
# uv, `just` and Node — so a reviewer can run the repo in a Codespace or any
# dev container tool without installing anything.
#
# Not the root `Dockerfile`. That one is the *service*: `UV_NO_SYNC`, no `dev`
# group, and `DAGSTER_HOME` pointed at the Postgres-backed instance a host must
# not use (docs/decisions/0011). This image carries the toolchain alone and the
# recipes run exactly as they do on a host, into the workspace.
#
# The uv and Node tags are the root Dockerfile's, and
# `tests/test_dagster_instance.py` holds them together; Dependabot's `docker` entry
# watches both directories, so one grouped PR moves both. Python is not here:
# uv reads `.python-version` and fetches CPython itself, as on a host.

FROM node:24.21.0-bookworm-slim AS node
FROM ghcr.io/astral-sh/uv:0.12.17 AS uv

FROM mcr.microsoft.com/devcontainers/base:2.2.1-bookworm

COPY --from=uv /uv /uvx /usr/local/bin/

# Node from the node image, as in the root Dockerfile, so the site is built with
# `pages.yml`'s major. `COPY` does not follow npm's and npx's symlinks out.
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# `uv tool install` puts `just` in ~/.local/bin, which a non-login shell does
# not have on PATH. UV_LINK_MODE because uv's cache and the bind-mounted
# workspace are different filesystems, where every hardlink falls back to a copy
# with a warning.
ENV PATH=/home/vscode/.local/bin:$PATH \
    UV_LINK_MODE=copy
