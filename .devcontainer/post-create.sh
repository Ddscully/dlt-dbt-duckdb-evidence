#!/usr/bin/env bash
# Runs once, when the dev container is created: the Quickstart's `just setup`,
# plus the manifest `just dagster` reads at import.
set -euo pipefail

# `uv.lock`'s `just`, read the way `.github/actions/setup` reads it: the `deploy`
# group pins it for the image, and syncing that group here would be undone by
# `just setup`, which syncs `dev` and `orchestration` alone.
just_pin=$(uv export --frozen --no-hashes --no-header --only-group deploy | grep -E '^rust-just==')
uv tool install "$just_pin"

just setup
just dbt-parse
