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
    uv run python -m transform.retail_rfm

# Pipeline observability tables (load times, layer inventory, dbt test failures)
# Must run after dbt-build: it reads dbt_test__audit and dbt/target/manifest.json.
pipeline-status:
    uv run python -m transform.pipeline_status

# Full pipeline via shell ordering (see `just materialize` for the graph-aware one)
run: ingest dbt-build transform pipeline-status lake

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
    uv run python -m transform.retail_rfm
    uv run python -m transform.pipeline_status
    uv run python -m lake.archive

# `.github/workflows/release-data.yml` runs this, then attaches the result to a
# dated GitHub release.
# The salt pseudonymises the classified identifiers on the way out and the
# exporter refuses to run without one. A local export gets a throwaway, which is
# right for a local export: it is not the artifact anyone publishes, and a
# per-run salt makes that visible rather than letting a laptop's copy look like a
# release. `release-data.yml` passes the stable repository secret instead — see
# `docs/DATA_PROTECTION.md` for why that one has to be stable.
#
# Package data/export/ for publishing: DuckDB copy, Parquet, checksums, notes
export-data:
    PII_SALT="${PII_SALT:-$(uv run python -c 'import secrets; print(secrets.token_hex(32))')}" \
        uv run python -m scripts.export_warehouse

# Prints the table in docs/DATA_PROTECTION.md from the warehouse, so it can be
# re-checked when the models move. Read-only.
#
# How identifiable is a customer once the identifier is gone?
disclosure-risk:
    uv run python -m scripts.measure_disclosure_risk

# Copy `history` out of a published release into this warehouse, so `dbt build`
# appends to that snapshot instead of starting a new one. release-data.yml runs
# this before it builds; locally it's how you get real revision history without
# waiting a month for OWID:
#   gh release download --pattern warehouse.duckdb --dir prev
#   just restore-history prev/warehouse.duckdb
restore-history from:
    uv run python -m scripts.restore_history {{ from }}

# Re-record the fixtures from the live APIs (hits the network; commit the diff)
record-fixtures:
    uv run python -m scripts.record_fixtures

# Dagster UI on :3000 — asset graph, run history, freshness, checks
dagster:
    mkdir -p "$DAGSTER_HOME"
    uv run --group orchestration dagster dev

# Full pipeline, ordered by the asset graph and recorded in the Dagster instance.
# Excludes the Evidence site, which needs Node — see `just materialize-site`.
#
# Two jobs, because an asset job takes a single partitions definition and the
# retail ingest is monthly where wb_wdi is yearly (see orchestration/definitions.py).
# `load_retail` has to come first: dbt reads raw.retail_invoice_lines.
materialize:
    mkdir -p "$DAGSTER_HOME"
    uv run --group orchestration dagster job execute -m orchestration.definitions -j load_retail
    uv run --group orchestration dagster job execute -m orchestration.definitions -j full_refresh

# The same graph plus the Evidence site on the end of it (requires Node).
# This is what .github/workflows/pages.yml runs.
materialize-site:
    mkdir -p "$DAGSTER_HOME"
    uv run --group orchestration dagster job execute -m orchestration.definitions -j load_retail
    uv run --group orchestration dagster job execute -m orchestration.definitions -j publish_site

# Materialize a selection, e.g. `just materialize-select 'raw/wb_wdi*'` (* = all downstream, + = one layer)
materialize-select selection:
    mkdir -p "$DAGSTER_HOME"
    uv run --group orchestration dagster asset materialize \
        -m orchestration.definitions --select '{{ selection }}'

# Re-load WDI for one year or a range of years: `just backfill-wdi 1995` or
# `just backfill-wdi 1990 1995`. Asks the World Bank for exactly that window and
# merges it in, so it is re-runnable — the same year loaded twice leaves the same
# table. Only `raw/wb_wdi` is partitioned, so this can't pull the downstream
# models along (the CLI rejects a range over unpartitioned assets); follow with
# `just dbt-build` or `just materialize`.
backfill-wdi start end='':
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p "$DAGSTER_HOME"
    end="{{ end }}"
    uv run --group orchestration dagster asset materialize \
        -m orchestration.definitions --select 'raw/wb_wdi' \
        --partition-range "{{ start }}...${end:-{{ start }}}"

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

# Build the Evidence dashboard (requires Node; see reports/README.md).
# Wraps the same module the `reports/evidence_site` asset calls, so the recipe and
# the asset graph can't run different builds.
report:
    uv run python -m scripts.build_report

# Evidence caches each source's schema keyed on the source SQL, so a `select *`
# that gains columns looks unchanged and validation fails against the stale schema.
# Nuke the cache + reprocess sources, then build. Use after mart columns change.
report-clean:
    uv run python -m scripts.build_report --clean

# ---------------------------------------------------------------------------
# Course (docs/course/) — the sandbox the exercises break on purpose
# ---------------------------------------------------------------------------

# Same offline, deterministic build as `just test-pipeline`, but at a *stable*
# path instead of a mktemp one — a drill breaks a model, looks at the wrong
# number, then fixes it, and the warehouse has to still be there on the next
# command.
#
# It is the 17-country fixture slice, which the course leans on rather than
# apologises for: a threshold those 17 pass and the full 200+ would break is one
# of the failures the material is about. Investigate-the-data exercises read the
# real warehouse instead.
#
# Build the course sandbox in data/course/ (gitignored) from the fixtures
course-sandbox:
    #!/usr/bin/env bash
    set -euo pipefail
    export INGEST_FIXTURES=1
    # Absolute, or dbt (which runs from dbt/) and the Python layers (which run
    # from the repo root) resolve it to two different files.
    export WAREHOUSE_PATH="{{ justfile_directory() }}/data/course/warehouse.duckdb"
    export LAKE_DIR="{{ justfile_directory() }}/data/course/lake"
    mkdir -p "$(dirname "$WAREHOUSE_PATH")"
    rm -f "$WAREHOUSE_PATH" "$WAREHOUSE_PATH.wal"
    echo "course sandbox: $WAREHOUSE_PATH"
    uv run python -m ingest.pipeline
    cd dbt && uv run dbt deps && uv run dbt build && cd ..
    uv run python -m transform.co2_intensity
    uv run python -m transform.retail_rfm
    uv run python -m transform.pipeline_status
    uv run python -m lake.archive
    echo "sandbox ready — 'just course-rebuild' after you change a model"

# Seconds rather than a full `just course-sandbox`, because the fixtures have
# already landed and nothing about a broken *model* requires re-ingesting.
#
# The drill inner loop: rebuild the dbt layer against the sandbox
course-rebuild:
    #!/usr/bin/env bash
    set -euo pipefail
    export WAREHOUSE_PATH="{{ justfile_directory() }}/data/course/warehouse.duckdb"
    test -f "$WAREHOUSE_PATH" || { echo "no sandbox yet — run: just course-sandbox" >&2; exit 1; }
    cd dbt && uv run dbt build

# The Polars layer runs *after* dbt and is not part of `just course-rebuild`, so
# a drill on a derived metric (module 04's denominator) needs this instead. It is
# a recipe rather than a line in the material for one reason: the raw form is
# `WAREHOUSE_PATH=... uv run python -m transform.co2_intensity`, and a learner who
# forgets the variable rewrites `analytics` in the *real* warehouse.
#
# Re-run the Polars derived metrics against the course sandbox
course-transform:
    #!/usr/bin/env bash
    set -euo pipefail
    export WAREHOUSE_PATH="{{ justfile_directory() }}/data/course/warehouse.duckdb"
    test -f "$WAREHOUSE_PATH" || { echo "no sandbox yet — run: just course-sandbox" >&2; exit 1; }
    uv run python -m transform.co2_intensity
    uv run python -m transform.retail_rfm

# Read-only on purpose: DuckDB takes one writer at a time, so a REPL left open is
# what makes the next `just course-rebuild` fail on a lock.
#   just course-query 'select count(*) from marts.dim_country_year'
#
# Run one read-only query against the course sandbox
course-query sql:
    @uv run python -c "import duckdb,sys; \
        print(duckdb.connect('{{ justfile_directory() }}/data/course/warehouse.duckdb', read_only=True).sql(sys.argv[1]))" \
        {{ quote(sql) }}
