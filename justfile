# Lightweight orchestration. `just <recipe>`; run `just` to list.
# (Install: `uv tool install rust-just` or use your package manager.)
#
# `just --list` shows only the last comment line before a recipe, so each
# recipe's one-line summary ends its comment block, above the `[group]` line.
# Every public recipe takes a group, and `default` lists them unsorted: a group
# appears where its first recipe does, and its recipes in file order, so
# `ingest` stays ahead of `dbt-build`. A recipe without a group lists above them all.

set dotenv-load := true

# Dagster run/event storage (gitignored except dagster.yaml). `env(...)` so a
# caller's value wins — `.github/actions/setup` sets it in CI.
export DAGSTER_HOME := env("DAGSTER_HOME", justfile_directory() / ".dagster")

# Absolute on purpose. DuckLake records the catalog's `data_path` as given and
# compares it as a string on every attach; dbt runs from `dbt/` and the Python
# layers from the repo root, so one relative path becomes two strings and the
# attach is refused. The warehouse file needs no such care: it records no path.
export LAKEHOUSE_DIR := env("LAKEHOUSE_DIR", justfile_directory() / "data/lakehouse")

[private]
default:
    @just --list --unsorted

# dbt's log names the target, never the file, so every recipe that writes
# depends on this — except those exporting their own WAREHOUSE_PATH.
# Print which warehouse file and landing zone the pipeline recipes will use
[group('setup')]
where: _no-dbt-dotenv
    @echo "warehouse: ${WAREHOUSE_PATH:-(unset - this repo's data/warehouse.duckdb)}"
    @echo "lakehouse: $LAKEHOUSE_DIR"
    @echo "lakehouse data: ${LAKEHOUSE_DATA_PATH:-(unset - $LAKEHOUSE_DIR/data/)}"
    @echo "lakehouse catalog: ${LAKEHOUSE_CATALOG:-(unset - $LAKEHOUSE_DIR/catalog.duckdb)}${LAKEHOUSE_CATALOG:+ (schema ${LAKEHOUSE_METADATA_SCHEMA:-lakehouse})}"

# Every writing recipe depends on this, through `where` or directly. dbt loads a
# .env from dbt/, filling any variable the shell leaves unset, so dbt would build
# against a file no recipe printed. The repo-root .env is exported, so `where`
# shows it.
_no-dbt-dotenv:
    @if [ -e "{{ justfile_directory() }}/dbt/.env" ]; then \
        echo "refusing: dbt/.env exists, and dbt reads it for any variable this recipe leaves unset." >&2; \
        echo "Move its values to the repo-root .env, which just exports and 'just where' prints, then delete it." >&2; \
        exit 1; \
    fi

# One-time: install runtime + dev deps into the uv-managed venv, and the DuckDB extensions
[group('setup')]
setup:
    uv sync --group dev --group orchestration
    just extensions

# Binaries no lockfile can name; installing them here moves the download out of a
# `dbt build` inside a Dagster op (the-lakehouse skill). httpfs and postgres are
# inert until LAKEHOUSE_DATA_PATH or LAKEHOUSE_CATALOG is set.
# Install the DuckDB extensions the lakehouse can need
[group('setup')]
extensions:
    uv run python -c "import duckdb; con = duckdb.connect(); [con.execute(f'install {e}') for e in ('ducklake', 'httpfs', 'postgres')]"

# Names all three groups: `uv sync` computes the whole venv, so `--group deploy`
# alone would strip dev and orchestration. Why `setup` leaves it out, and why a
# host must not use `deploy/`, is docs/decisions/0011.
# Add the `deploy` group, so the run-launcher tests run instead of skipping
[group('setup')]
deploy-deps:
    uv sync --group dev --group orchestration --group deploy

# EL: pull public sources into the DuckLake landing zone
[group('pipeline')]
ingest: where
    uv run python -m ingest.pipeline

# For a World Bank restatement older than the lookback window, or after the raw
# table was dropped while dlt's watermark survived.
# Re-fetch the whole WDI series instead of the incremental window
[group('pipeline')]
ingest-wdi-full: where
    INGEST_WDI_FULL=1 uv run python -m ingest.pipeline

# It lives in dlt's own directory, keyed on the pipeline name, so no warehouse
# query shows it. `just dlt-state modern_data_stack_fixtures` reads the fixtures'.
# Show dlt's incremental state — the WDI watermark and the ECB's last fixing
[group('inspect')]
dlt-state pipeline="modern_data_stack":
    uv run dlt pipeline {{ pipeline }} info -v

# The mkdir is needed because dbt's profile attaches the DuckLake catalog on
# every invocation (`dbt parse` and sqlfluff's templater included), and DuckLake
# will not create the catalog's parent directory.
# Install dbt packages (dbt_utils) into the gitignored dbt/dbt_packages/
[group('pipeline')]
dbt-deps:
    mkdir -p "$LAKEHOUSE_DIR"
    cd dbt && uv run dbt deps

# T: build + test dbt models
[group('pipeline')]
dbt-build: where dbt-deps
    cd dbt && uv run dbt build

# `@dbt_assets` reads dbt/target/manifest.json at import time and dbt/target/ is
# gitignored, so every headless `dagster` recipe depends on this. `just dagster`
# does not: `prepare_if_dev()` parses under the dev CLI.
# Write dbt/target/manifest.json — the Dagster graph won't load without it
[group('pipeline')]
dbt-parse: dbt-deps
    cd dbt && uv run dbt parse

# The models' parents must exist in the warehouse (schema only); run
# `just dbt-build` once if they don't.
# dbt unit tests only — mocked inputs, the inner loop for model logic
[group('check')]
dbt-unit-test: dbt-deps
    cd dbt && uv run dbt test --select test_type:unit

# Thresholds are in models/staging/_sources.yml. `_dlt_load_id` is stamped at
# ingest, so this measures when the pipeline last ran, not when a publisher last
# published.
# Is the warehouse stale? `dbt source freshness` against dlt's load ids
[group('inspect')]
dbt-freshness: dbt-deps
    cd dbt && uv run dbt source freshness

# Needs a built warehouse, or the catalog's columns come back untyped.
# Render the dbt metadata layer to dbt/target/ — columns, contracts, groups, exposures, versions, tests
[group('inspect')]
dbt-docs: dbt-deps
    cd dbt && uv run dbt docs generate

# Serve the dbt docs site on :8080, regenerated first (blocks; ctrl-c to stop)
[group('inspect')]
dbt-docs-serve: dbt-docs
    cd dbt && uv run dbt docs serve

# Report what the DuckLake landing zone holds — tables, rows, snapshots (read-only)
[group('inspect')]
lakehouse:
    uv run python -m lake.lakehouse

# Expiry is counted in weather loads, not days, so an idle catalog keeps the
# pair `weather_revisions_are_derivable` diffs (the-lakehouse). `just run` and
# `full_refresh` both end with it. `keep` defaults to `KEEP_WEATHER_LOADS`, so
# the constant is the one place the number lives.
# Expire lakehouse snapshots before the last `keep` weather loads, and their files
[group('pipeline')]
lakehouse-expire keep="": where
    uv run python -m lake.lakehouse --expire {{ keep }}

# Polars derived metrics
[group('pipeline')]
transform: where
    uv run python -m transform.co2_intensity
    uv run python -m transform.retail_rfm

# Run after dbt-build: it reads dbt_test__audit and dbt's artifacts.
# Pipeline observability tables (load times, layer inventory, dbt test failures)
[group('pipeline')]
pipeline-status: where
    uv run python -m transform.pipeline_status

# Full pipeline via shell ordering (see `just materialize` for the graph-aware one)
[group('pipeline')]
run: ingest dbt-build transform pipeline-status lakehouse-expire

# Unit tests — mocked API payloads, no network, no warehouse
[group('check')]
test:
    uv run pytest

# Two commands so a failing suite stops before a percentage is printed.
# Line + branch coverage of `just test` — reports, gates nothing
[group('check')]
coverage:
    uv run coverage run -m pytest
    uv run coverage report

# The whole pipeline against checked-in fixtures, into a throwaway warehouse — what CI runs
[group('check')]
test-pipeline: _no-dbt-dotenv
    #!/usr/bin/env bash
    set -euo pipefail
    export INGEST_FIXTURES=1
    # Every piece of state the next real command reads is redirected, or the
    # fixture run leaks into it: the warehouse, the landing zone (which holds
    # the weather archive no rebuild can afford), and dbt's artifacts (which
    # `pipeline-status` files into `analytics.pipeline_runs`).
    export WAREHOUSE_PATH="$(mktemp -d)/warehouse.duckdb"
    export LAKEHOUSE_DIR="$(dirname "$WAREHOUSE_PATH")/lakehouse"
    export DBT_TARGET_PATH="$(dirname "$WAREHOUSE_PATH")/dbt-target"
    export DBT_MANIFEST_PATH="$DBT_TARGET_PATH/manifest.json"
    export DBT_RUN_RESULTS_PATH="$DBT_TARGET_PATH/run_results.json"
    # A landing zone in a bucket keeps its storage and loses its prefix: the
    # fixture Parquet goes under `test-pipeline/<tmp>/` in the same bucket, so an
    # S3 setup is what gets tested, under a prefix no real run uses.
    if [[ "${LAKEHOUSE_DATA_PATH:-}" == s3://* ]]; then
      bucket="${LAKEHOUSE_DATA_PATH#s3://}"
      export LAKEHOUSE_DATA_PATH="s3://${bucket%%/*}/test-pipeline/$(basename "$(dirname "$WAREHOUSE_PATH")")/"
      echo "fixture lakehouse data: $LAKEHOUSE_DATA_PATH"
    fi
    # A Postgres catalog is shared, so the fixture run takes a metadata schema of
    # its own, in the `test_pipeline_[a-z0-9_]+` form `drop_fixture_schema` deletes.
    if [ -n "${LAKEHOUSE_CATALOG:-}" ]; then
      export LAKEHOUSE_METADATA_SCHEMA="test_pipeline_$(basename "$(dirname "$WAREHOUSE_PATH")" | tr -c 'a-z0-9\n' _)"
      echo "fixture lakehouse catalog schema: $LAKEHOUSE_METADATA_SCHEMA"
    fi
    echo "fixture warehouse: $WAREHOUSE_PATH"
    uv run python -m ingest.pipeline
    cd dbt && uv run dbt deps && uv run dbt build --target-path "$DBT_TARGET_PATH" && cd ..
    uv run python -m transform.co2_intensity
    uv run python -m transform.retail_rfm
    uv run python -m transform.pipeline_status
    # A second weather load, so expiry has a snapshot to expire: after one load it
    # expires nothing, and CI would never call DuckLake's expiry against Postgres
    # and a bucket, which the compose job runs this recipe on.
    uv run python -m ingest.pipeline om_weather_daily
    uv run python -c "from lake.lakehouse import expire; freed = expire(); print(freed); assert freed['snapshots'], 'expiry expired no snapshot'"
    uv run python -m lake.lakehouse
    # Last, and only on success: `set -e` stops a failed run before here, leaving
    # its schema to be inspected — the same bargain as the orphaned Parquet an S3
    # fixture run leaves in the bucket.
    if [ -n "${LAKEHOUSE_CATALOG:-}" ]; then
      uv run python -c "import os; from lake.lakehouse import drop_fixture_schema; drop_fixture_schema(os.environ['LAKEHOUSE_METADATA_SCHEMA'])"
      echo "dropped fixture schema: $LAKEHOUSE_METADATA_SCHEMA"
    fi

# The exporter refuses to run without PII_SALT. A local export gets a throwaway
# salt, so its pseudonyms cannot pass for a release's; `release-data.yml` passes
# the stable repository secret (docs/DATA_PROTECTION.md says why it is stable).
# Package data/export/ for publishing: DuckDB copy, Parquet, checksums, notes
[group('publish')]
export-data:
    PII_SALT="${PII_SALT:-$(uv run python -c 'import secrets; print(secrets.token_hex(32))')}" \
        uv run python -m publish.export_warehouse

# Reads dbt/target/manifest.json (`just dbt-parse`), never the warehouse: grains
# come from the uniqueness tests, columns from the enforced contracts.
# Which conformed dimensions does each fact carry? -> docs/WAREHOUSE.md
[group('publish')]
bus-matrix:
    uv run python -m publish.bus_matrix

# Reprints the table in docs/DATA_PROTECTION.md from the warehouse. Read-only.
# How identifiable is a customer once the identifier is gone?
[group('publish')]
disclosure-risk:
    uv run python -m scripts.measure_disclosure_risk

# Copies `history` and `analytics.pipeline_runs` so the build appends to them,
# plus the landing zone when `lakehouse.tar.gz` sits beside the file (refused
# while dlt has local state). `release-data.yml` runs it before building; locally:
#   gh release download --pattern warehouse.duckdb --dir prev
#   just restore-history prev/warehouse.duckdb
# Carry a published release's unreproducible tables into this warehouse
[group('publish')]
restore-history from: where
    uv run python -m publish.restore_history {{ from }}

# Re-record the fixtures from the live APIs (hits the network; commit the diff)
[group('check')]
record-fixtures:
    uv run python -m scripts.record_fixtures

# Its SupersessionWarning is expected: the dagster-graph-and-jobs skill says why.
# Dagster UI on :3000 — asset graph, run history, freshness, checks
[group('dagster')]
dagster:
    mkdir -p "$DAGSTER_HOME"
    uv run --group orchestration dagster dev

# Two jobs, `load_retail` first because dbt reads its table (dagster-graph-and-jobs).
# Full pipeline ordered by the asset graph, minus the Evidence site
[group('dagster')]
materialize: where dbt-parse
    mkdir -p "$DAGSTER_HOME"
    uv run --group orchestration dagster job execute -m orchestration.definitions -j load_retail
    uv run --group orchestration dagster job execute -m orchestration.definitions -j full_refresh

# What .github/workflows/pages.yml runs.
# The same graph with the Evidence site on the end of it (needs Node)
[group('dagster')]
materialize-site: where dbt-parse
    mkdir -p "$DAGSTER_HOME"
    uv run --group orchestration dagster job execute -m orchestration.definitions -j load_retail
    uv run --group orchestration dagster job execute -m orchestration.definitions -j publish_site

# A bare prefix is not a glob: `marts/*` means "downstream of the key `marts/`",
# matches nothing and exits 0. Write `key:"marts/*"`; `group:`, `kind:`,
# `sinks(...)` and `roots(...)` also work. Check with `just materialize-preview`.
# Materialize a selection, e.g. `just materialize-select 'raw/wb_wdi*'` (* = all downstream, + = one layer)
[group('dagster')]
materialize-select selection: where dbt-parse
    mkdir -p "$DAGSTER_HOME"
    uv run --group orchestration dagster asset materialize \
        -m orchestration.definitions --select '{{ selection }}'

# A selection matching no assets is not an error to `materialize`, so look first.
# Print the assets a selection resolves to, without materializing any of them
[group('dagster')]
materialize-preview selection: dbt-parse
    uv run --group orchestration dagster asset list \
        -m orchestration.definitions --select '{{ selection }}'

# `dbt-parse` first, or the location fails to load instead of reporting what is
# unregistered — which also makes this no check on a *running* service
# (RUNNING_AS_A_SERVICE.md §7). No `-m` (§8).
# Check the code location loads and every definition is registered
[group('dagster')]
validate: dbt-parse
    uv run --group orchestration dagster definitions validate

# `just backfill-wdi 1995` or `just backfill-wdi 1990 1995`; merges, then follow
# with `just dbt-build`. The years are run config, not partitions
# (docs/decisions/0002), and `tests/test_definitions.py` holds the op name.
# Re-load WDI for one year or a range of years
[group('dagster')]
backfill-wdi start end='': where dbt-parse
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p "$DAGSTER_HOME"
    end="{{ end }}"
    config=$(printf '{"ops": {"ingest_by_year": {"config": {"first_year": %d, "last_year": %d}}}}' \
        "{{ start }}" "${end:-{{ start }}}")
    uv run --group orchestration dagster asset materialize \
        -m orchestration.definitions --select 'raw/wb_wdi' --config-json "$config"

# Back to 1960, paced to Open-Meteo's budget: fifteen years is the most one run
# can hold, and a longer range is refused before any request (weather-models).
# Follow with `just dbt-build`.
# Deepen the capital-city weather archive, e.g. `just backfill-weather 2012 2026`
[group('dagster')]
backfill-weather start end='': where dbt-parse
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p "$DAGSTER_HOME"
    end="{{ end }}"
    config=$(printf '{"ops": {"ingest_by_year": {"config": {"first_year": %d, "last_year": %d}}}}' \
        "{{ start }}" "${end:-{{ start }}}")
    uv run --group orchestration dagster asset materialize \
        -m orchestration.definitions --select 'raw/om_weather_daily' --config-json "$config"

# Either mode blocks a build while open (querying-the-warehouse). It attaches the
# lakehouse, which the staging views read, and any S3 secret via `getenv`, keeping
# the keys out of the process list (the-lakehouse).
# Open the warehouse in the DuckDB CLI (`just sql write` for a writer)
[group('inspect')]
sql mode="read":
    #!/usr/bin/env bash
    set -euo pipefail
    warehouse="${WAREHOUSE_PATH:-data/warehouse.duckdb}"
    data="${LAKEHOUSE_DATA_PATH:-$LAKEHOUSE_DIR/data/}"
    secret=""
    if [ -n "${LAKEHOUSE_DATA_PATH:-}" ]; then
      unset_msg="is unset, and LAKEHOUSE_DATA_PATH names a bucket (see .env.example)"
      endpoint="${LAKEHOUSE_S3_ENDPOINT:?$unset_msg}"
      : "${AWS_ACCESS_KEY_ID:?$unset_msg}" "${AWS_SECRET_ACCESS_KEY:?$unset_msg}"
      ssl=true; [[ "$endpoint" == http://* ]] && ssl=false
      host="${endpoint#*://}"
      secret="install httpfs; load httpfs; create secret (type s3, key_id getenv('AWS_ACCESS_KEY_ID'), secret getenv('AWS_SECRET_ACCESS_KEY'), endpoint '${host%/}', use_ssl $ssl, region '${AWS_REGION:-us-east-1}', url_style 'path', scope '$data');"
    fi
    # The catalog is a file, or Postgres. Nothing secret enters this string
    # either way: libpq reads PGPASSWORD, which `set dotenv-load` has already put
    # in the environment, so only the S3 keys need the `getenv` trick above.
    catalog="ducklake:duckdb:$LAKEHOUSE_DIR/catalog.duckdb"
    meta=", metadata_schema 'main'"
    pg=""
    if [ -n "${LAKEHOUSE_CATALOG:-}" ]; then
      catalog="ducklake:postgres:$LAKEHOUSE_CATALOG"
      meta=", metadata_schema '${LAKEHOUSE_METADATA_SCHEMA:-lakehouse}'"
      pg="install postgres; load postgres;"
    fi
    attach="install ducklake; load ducklake; $pg $secret attach '$catalog' as lakehouse (data_path '$data'$meta"
    if [ "{{ mode }}" = "write" ]; then
      uv run duckdb "$warehouse" -cmd "$attach);"
    else
      uv run duckdb -readonly "$warehouse" -cmd "$attach, read_only);"
    fi

# From dbt/, because the dbt templater resolves the profile's relative
# `../data/…` default against the working directory.
# Lint the dbt models and snapshots with sqlfluff
[group('check')]
lint: dbt-deps
    cd dbt && uv run sqlfluff lint models snapshots

# ty is pre-1.0 and runs in neither pre-commit nor CI; `uv run` so the locked
# version answers. Suppressions go inline as `# ty: ignore[rule]`.
# Type-check the Python — reports, gates nothing
[group('check')]
typecheck:
    uv run ty check

# The same module the `reports/evidence_site` asset calls.
# Build the Evidence dashboard (requires Node; see reports/README.md)
[group('publish')]
report: dbt-deps
    uv run python -m publish.build_report

# Evidence caches each source's schema and does not notice a column change, so
# use this rather than `report` after any mart or analytics column changes.
# Drop Evidence's schema cache, re-extract the sources, then build
[group('publish')]
report-clean: dbt-deps
    uv run python -m publish.build_report --clean

# ---------------------------------------------------------------------------
# Running as a service (docs/RUNNING_AS_A_SERVICE.md)
# ---------------------------------------------------------------------------

# `env(...)` so a deployment's EnvironmentFile wins. `publish/build_report.py`
# empties reports/build on every run, so the site is down while it rebuilds —
# §4 of the design swaps a symlink instead.
export SITE_ROOT := env("SITE_ROOT", justfile_directory() / "reports/build")

# Every choice here — no `dagster dev`, `dbt-parse`, the bind addresses, `wait -n`,
# killing recorded PIDs — is §2 of docs/RUNNING_AS_A_SERVICE.md. The
# `daily_refresh` schedule ships STOPPED (§10).
# Run the graph and the dashboard as one always-on service (blocks; ctrl-c to stop)
[group('service')]
serve dagster_port="3000" site_port="8081" host="127.0.0.1": where dbt-parse
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p "$DAGSTER_HOME"
    test -d "$SITE_ROOT" || { echo "no site at $SITE_ROOT — run: just report" >&2; exit 1; }

    pids=()
    stop() {
        trap - EXIT INT TERM
        [ ${#pids[@]} -eq 0 ] || kill "${pids[@]}" 2>/dev/null || true
        wait 2>/dev/null || true
    }
    # Stopped by a signal: exit 0. A child exiting on its own: exit 1 (below), so
    # a supervisor restarts on failure and not on `systemctl stop`.
    trap 'stop; exit 0' INT TERM
    trap stop EXIT

    uv run --group orchestration dagster-webserver -h {{ host }} -p {{ dagster_port }} &
    pids+=($!)
    uv run --group orchestration dagster-daemon run &
    pids+=($!)
    uv run --group orchestration python -m http.server {{ site_port }} --directory "$SITE_ROOT" &
    pids+=($!)

    echo "dagster: http://{{ host }}:{{ dagster_port }}    site: http://0.0.0.0:{{ site_port }} (every interface) — $SITE_ROOT"

    status=0
    wait -n || status=$?
    echo "serve: a child process exited (status $status) — stopping the rest" >&2
    exit 1

# Postgres, SeaweedFS, the graph and nginx — optional: with LAKEHOUSE_CATALOG and
# LAKEHOUSE_DATA_PATH unset, none of it runs. `just compose-build` first; Dagster
# is then on :3000, the dashboard on :8081 (docs/RUNNING_AS_A_SERVICE.md).
# Start the stack and wait for it to be healthy
[group('service')]
compose-up:
    docker compose up -d --wait

# Its CMD is `serve`. Compose does not rebuild, so build after any change to the tree.
# Build the mds:local image compose runs
[group('service')]
compose-build:
    docker compose build

# The one command that exercises a remote catalog, a remote data path and the
# container together.
# Run the fixture pipeline inside the container (needs `just compose-up`)
[group('service')]
compose-test-pipeline:
    docker compose run --rm --no-deps dagster just test-pipeline

# Returns once the run is *queued*: its exit code says nothing about the run, and
# a second launch does not wait for the first to succeed (RUNNING_AS_A_SERVICE.md
# §10). No `where`: the run uses the `mds_data` volume, not the host's warehouse.
# No `-m` (§8).
# Queue a job on the compose stack's daemon (needs `just compose-up`)
[group('service')]
compose-launch job:
    docker compose exec -T dagster uv run dagster job launch -j {{ job }}

# `just compose-down volumes` also deletes the named volumes — which destroys
# the catalog and the bucket, and is the only way to make the Postgres init
# script run again (the entrypoint runs it on an empty data directory alone).
# Stop the stack
[group('service')]
compose-down mode="keep":
    #!/usr/bin/env bash
    set -euo pipefail
    if [ "{{ mode }}" = "volumes" ]; then
      docker compose down -v
    else
      docker compose down
    fi

# ---------------------------------------------------------------------------
# Course (docs/course/) — the sandbox the exercises break on purpose
# ---------------------------------------------------------------------------

# `test-pipeline` at a stable path, so a deliberately broken model is still there
# on the next command. It is the 17-country fixture slice; exercises that
# investigate the data read the real warehouse instead.
# Build the course sandbox in data/course/ (gitignored) from the fixtures
[group('course')]
course-sandbox: _no-dbt-dotenv
    #!/usr/bin/env bash
    set -euo pipefail
    export INGEST_FIXTURES=1
    # Absolute, or dbt (which runs from dbt/) and the Python layers (which run
    # from the repo root) resolve it to two different files.
    export WAREHOUSE_PATH="{{ justfile_directory() }}/data/course/warehouse.duckdb"
    export LAKEHOUSE_DIR="{{ justfile_directory() }}/data/course/lakehouse"
    # On disk whatever the real landing zone is on: a bucket data path would
    # outrank LAKEHOUSE_DIR and put the slice's Parquet beside the real files,
    # and a Postgres catalog would put the slice's tables in the real catalog.
    unset LAKEHOUSE_DATA_PATH LAKEHOUSE_CATALOG
    # dbt writes its artifacts to dbt/target/ whichever warehouse it built, and
    # the next `just pipeline-status` files that run_results.json in the real
    # warehouse's carried build history. The sandbox keeps its own.
    export DBT_TARGET_PATH="{{ justfile_directory() }}/data/course/dbt-target"
    export DBT_MANIFEST_PATH="$DBT_TARGET_PATH/manifest.json"
    export DBT_RUN_RESULTS_PATH="$DBT_TARGET_PATH/run_results.json"
    mkdir -p "$(dirname "$WAREHOUSE_PATH")"
    rm -f "$WAREHOUSE_PATH" "$WAREHOUSE_PATH.wal"
    echo "course sandbox: $WAREHOUSE_PATH"
    uv run python -m ingest.pipeline
    cd dbt && uv run dbt deps && uv run dbt build --target-path "$DBT_TARGET_PATH" && cd ..
    uv run python -m transform.co2_intensity
    uv run python -m transform.retail_rfm
    uv run python -m transform.pipeline_status
    echo "sandbox ready — 'just course-rebuild' after you change a model"

# No re-ingest: a broken model needs only the dbt layer rebuilt.
# The drill inner loop: rebuild the dbt layer against the sandbox
[group('course')]
course-rebuild: _no-dbt-dotenv
    #!/usr/bin/env bash
    set -euo pipefail
    export WAREHOUSE_PATH="{{ justfile_directory() }}/data/course/warehouse.duckdb"
    # dbt attaches $LAKEHOUSE_DIR and every staging model is a view over it, so
    # left at the real landing zone a drill rebuilds the sandbox's marts from the
    # full data, and the build is green.
    export LAKEHOUSE_DIR="{{ justfile_directory() }}/data/course/lakehouse"
    unset LAKEHOUSE_DATA_PATH LAKEHOUSE_CATALOG  # the sandbox is on disk (see course-sandbox)
    export DBT_TARGET_PATH="{{ justfile_directory() }}/data/course/dbt-target"  # see course-sandbox
    test -f "$WAREHOUSE_PATH" || { echo "no sandbox yet — run: just course-sandbox" >&2; exit 1; }
    cd dbt && uv run dbt build --target-path "$DBT_TARGET_PATH"

# `course-rebuild` stops at dbt. A recipe rather than a command in the material,
# because the raw form forgets WAREHOUSE_PATH once and rewrites the real
# warehouse's `analytics`.
# Re-run the Polars derived metrics against the course sandbox
[group('course')]
course-transform: _no-dbt-dotenv
    #!/usr/bin/env bash
    set -euo pipefail
    export WAREHOUSE_PATH="{{ justfile_directory() }}/data/course/warehouse.duckdb"
    test -f "$WAREHOUSE_PATH" || { echo "no sandbox yet — run: just course-sandbox" >&2; exit 1; }
    uv run python -m transform.co2_intensity
    uv run python -m transform.retail_rfm

# One query and exit, so no session holds the file when `course-rebuild` needs it:
#   just course-query 'select count(*) from marts.dim_country_year'
# It attaches the sandbox lakehouse as `just sql` does; landing tables are
# `lakehouse.raw.<table>`.
# Run one read-only query against the course sandbox
[group('course')]
course-query sql:
    #!/usr/bin/env bash
    set -euo pipefail
    export LAKEHOUSE_DIR="{{ justfile_directory() }}/data/course/lakehouse"
    unset LAKEHOUSE_DATA_PATH LAKEHOUSE_CATALOG  # the sandbox is on disk (see course-sandbox)
    uv run python -c "import duckdb, sys; \
        from lake.lakehouse import LAKEHOUSE_DIR, attach_lakehouse; \
        con = duckdb.connect('{{ justfile_directory() }}/data/course/warehouse.duckdb', read_only=True); \
        attach_lakehouse(con, LAKEHOUSE_DIR, read_only=True); \
        print(con.sql(sys.argv[1]))" \
        {{ quote(sql) }}

# Everything this deletes is regenerable except data/warehouse.duckdb, whose
# `history` snapshots and `analytics.pipeline_runs` no rebuild reproduces — so
# that one is its own scope and is gated. `deep` adds reports/node_modules
# (restored by `just report`, needs Node).
# Reclaim gitignored build output (`deep` adds node_modules; `warehouse` needs --force)
[group('setup')]
clean scope="safe" force="":
    #!/usr/bin/env bash
    set -euo pipefail
    cd "{{ justfile_directory() }}"

    # `just clean warehouse [--force]`: gated before anything is deleted. The
    # count is `irreplaceable_rows()`, the same one `restore-history` and
    # `release-data.yml` use, so the three cannot disagree about what is
    # unreproducible. Passed the path explicitly because the deletion below names
    # data/warehouse.duckdb, whatever WAREHOUSE_PATH says.
    if [ "{{ scope }}" = "warehouse" ]; then
      if [ ! -e data/warehouse.duckdb ]; then
        echo "  data/warehouse.duckdb is already gone"
      else
        count='from publish.restore_history import irreplaceable_rows; print(irreplaceable_rows("data/warehouse.duckdb"))'
        held=$(uv run python -c "$count") || held=""
        # Fail closed on an unreadable count: `[ "" -gt 0 ]` inside an `if` is
        # false, not an error. `--force` does not override this, because a
        # corrupt file and one a running job holds locked look the same here.
        case "$held" in
          ''|*[!0-9]*)
            echo "could not count the unreproducible rows in data/warehouse.duckdb" >&2
            echo "(locked by another process?) — refusing to delete it" >&2
            exit 1
            ;;
        esac
        if [ "$held" -gt 0 ] && [ "{{ force }}" != "--force" ]; then
          echo "data/warehouse.duckdb holds $held rows a rebuild cannot make again" >&2
          echo "(snapshot history and dbt run history). Pass --force if" >&2
          echo "that is really what you want:" >&2
          echo "  just clean warehouse --force" >&2
          echo "A published release can restore some of it: just restore-history <file>" >&2
          exit 1
        fi
      fi
    fi

    freed=0
    drop() {
      for target in "$@"; do
        [ -e "$target" ] || continue
        size=$(du -sm "$target" 2>/dev/null | cut -f1)
        rm -rf "$target"
        freed=$((freed + size))
        printf '  removed %-28s %5s MB\n' "$target" "$size"
      done
    }

    # Regenerable, holding no state:
    #   dbt/target        `dbt parse` / `just dbt-build`   (the manifest)
    #   dbt/dbt_packages  `just dbt-deps`
    #   dbt/logs          any dbt command
    #   data/export       `just export-data`
    #   data/course       `just course-sandbox`
    #   data/cache        re-downloaded on the next ingest
    #   reports/build     `just report`
    #   reports/.evidence `just report-clean`
    # data/lake is dead: the hive archive DuckLake replaced wrote it, and nothing
    # reads it.
    #
    # data/lakehouse is deliberately absent — it is the only copy of every
    # landing table, including the weather archive. `drop` takes exact paths and
    # never globs, which is what keeps `data/lake` from reaching it.
    drop dbt/target dbt/dbt_packages dbt/logs \
         data/export data/course data/cache data/lake \
         reports/build reports/.evidence

    # Dagster run/event storage. `.dagster/dagster.yaml` is checked in and stays.
    find .dagster -mindepth 1 -maxdepth 1 ! -name dagster.yaml -exec rm -rf {} + 2>/dev/null || true

    if [ "{{ scope }}" = "deep" ]; then
      drop reports/node_modules
    fi

    if [ "{{ scope }}" = "warehouse" ] && [ -e data/warehouse.duckdb ]; then
      drop data/warehouse.duckdb data/warehouse.duckdb.wal
    fi

    printf 'freed %s MB\n' "$freed"
