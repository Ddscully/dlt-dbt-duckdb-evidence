# Lightweight orchestration. `just <recipe>`; run `just` to list.
# (Install: `uv tool install rust-just` or use your package manager.)

set dotenv-load := true

# Dagster keeps its run/event storage here (gitignored except dagster.yaml).
export DAGSTER_HOME := justfile_directory() / ".dagster"

default:
    @just --list

# One-time: install runtime + dev deps into the uv-managed venv
setup:
    uv sync --group dev --group notebook --group orchestration

# EL: pull public sources into DuckDB
ingest:
    uv run python -m ingest.pipeline

# Same, but re-fetch the whole WDI series instead of the incremental window.
# For a World Bank restatement older than the 5-year lookback (or to rebuild
# history after the raw table was dropped out from under the watermark).
ingest-wdi-full:
    INGEST_WDI_FULL=1 uv run python -m ingest.pipeline

# Install dbt packages (dbt_utils) into dbt/dbt_packages/ — gitignored, so this
# is needed once per clone and after any packages.yml change
dbt-deps:
    cd dbt && uv run dbt deps

# T: build + test dbt models
dbt-build: dbt-deps
    cd dbt && uv run dbt build

# Is the warehouse stale? Compares dlt's `_dlt_load_id` against the thresholds
# in models/staging/_sources.yml (warn at 7 days, error at 30). This measures
# when the pipeline last ran, not when the publishers last updated.
dbt-freshness: dbt-deps
    cd dbt && uv run dbt source freshness

# Write the year-partitioned Parquet archive to data/lake/ (gitignored)
lake:
    uv run python -m lake.archive

# Polars derived metrics
transform:
    uv run python -m transform.co2_intensity

# Full pipeline via shell ordering (see `just materialize` for the graph-aware one)
run: ingest dbt-build transform lake

# Unit tests — mocked API payloads, no network, no warehouse
test:
    uv run pytest

# The whole pipeline against checked-in fixtures, into a throwaway warehouse.
# This is what CI runs on a pull request: deterministic, offline, ~30s.
test-pipeline:
    #!/usr/bin/env bash
    set -euo pipefail
    export INGEST_FIXTURES=1
    export WAREHOUSE_PATH="$(mktemp -d)/warehouse.duckdb"
    # ...and the lake beside it, or a fixture run would overwrite data/lake/
    # with the 17-country slice.
    export LAKE_DIR="$(dirname "$WAREHOUSE_PATH")/lake"
    echo "fixture warehouse: $WAREHOUSE_PATH"
    uv run python -m ingest.pipeline
    cd dbt && uv run dbt deps && uv run dbt build && cd ..
    uv run python -m transform.co2_intensity
    uv run python -m lake.archive

# `.github/workflows/release-data.yml` runs this, then attaches the result to a
# dated GitHub release.
# Package data/export/ for publishing: DuckDB copy, Parquet, checksums, notes
export-data:
    uv run python -m scripts.export_warehouse

# Re-record the fixtures from the live APIs (hits the network; commit the diff)
record-fixtures:
    uv run python -m scripts.record_fixtures

# Dagster UI on :3000 — asset graph, run history, freshness, checks
dagster:
    mkdir -p "$DAGSTER_HOME"
    uv run --group orchestration dagster dev

# Full pipeline, ordered by the asset graph and recorded in the Dagster instance
materialize:
    mkdir -p "$DAGSTER_HOME"
    uv run --group orchestration dagster job execute -m orchestration.definitions -j full_refresh

# Materialize a selection, e.g. `just materialize-select 'raw/wb_wdi*'` (* = all downstream, + = one layer)
materialize-select selection:
    mkdir -p "$DAGSTER_HOME"
    uv run --group orchestration dagster asset materialize \
        -m orchestration.definitions --select '{{ selection }}'

# Open the warehouse in Harlequin (terminal SQL IDE)
sql:
    uv run harlequin data/warehouse.duckdb

# Reactive exploration notebook
notebook:
    uv run --group notebook marimo edit notebooks/explore.py

# Lint SQL — from dbt/, because the dbt templater opens the warehouse via the
# profile's relative path (`../data/…`) without chdir'ing into the project first
lint: dbt-deps
    cd dbt && uv run sqlfluff lint models snapshots

# Build the Evidence dashboard (requires Node; see reports/README.md)
report:
    cd reports && npm install && npm run sources && npm run build

# Evidence caches each source's schema keyed on the source SQL, so a `select *`
# that gains columns looks unchanged and validation fails against the stale schema.
# Nuke the cache + reprocess sources, then build. Use after mart columns change.
report-clean:
    cd reports && rm -rf .evidence build && npm install && npm run sources && npm run build
