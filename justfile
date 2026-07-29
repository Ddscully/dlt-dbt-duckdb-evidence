# Lightweight orchestration. `just <recipe>`; run `just` to list.
# (Install: `uv tool install rust-just` or use your package manager.)

set dotenv-load := true

default:
    @just --list

# One-time: install runtime + dev deps into the uv-managed venv
setup:
    uv sync --group dev --group notebook

# EL: pull public sources into DuckDB
ingest:
    uv run python -m ingest.pipeline

# T: build + test dbt models
dbt-build:
    cd dbt && uv run dbt build

# Polars derived metrics
transform:
    uv run python -m transform.co2_intensity

# Full pipeline, end to end
run: ingest dbt-build transform

# Open the warehouse in Harlequin (terminal SQL IDE)
sql:
    uv run harlequin data/warehouse.duckdb

# Reactive exploration notebook
notebook:
    uv run --group notebook marimo edit notebooks/explore.py

# Lint SQL — from dbt/, because the dbt templater opens the warehouse via the
# profile's relative path (`../data/…`) without chdir'ing into the project first
lint:
    cd dbt && uv run sqlfluff lint models

# Build the Evidence dashboard (requires Node; see reports/README.md)
report:
    cd reports && npm install && npm run build

# Evidence caches each source's schema keyed on the source SQL, so a `select *`
# that gains columns looks unchanged and validation fails against the stale schema.
# Nuke the cache + reprocess sources, then build. Use after mart columns change.
report-clean:
    cd reports && rm -rf .evidence build && npm install && npm run sources && npm run build
