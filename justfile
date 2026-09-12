# Lightweight orchestration. `just <recipe>`; run `just` to list.
# (Install: `uv tool install rust-just` or use your package manager.)
#
# `just --list` shows only the comment line directly above a recipe, so each
# recipe's one-line summary is the last line of its comment block.

set dotenv-load := true

# Dagster run/event storage (gitignored except dagster.yaml). `env(...)` so a
# caller's value wins — `.github/actions/setup` sets it in CI.
export DAGSTER_HOME := env("DAGSTER_HOME", justfile_directory() / ".dagster")

# Absolute on purpose. DuckLake records the catalog's `data_path` as given and
# compares it as a string on every attach; dbt runs from `dbt/` and the Python
# layers from the repo root, so one relative path becomes two strings and the
# attach is refused. The warehouse file needs no such care: it records no path.
export LAKEHOUSE_DIR := env("LAKEHOUSE_DIR", justfile_directory() / "data/lakehouse")

default:
    @just --list

# dbt's log names the target (`target='dev'`, the only one) and never the file,
# so every recipe that writes to the warehouse or the landing zone depends on
# this. The recipes that export their own WAREHOUSE_PATH (`test-pipeline`, the
# course ones) do not: this would print the outer value.
# Print which warehouse file and landing zone the pipeline recipes will use
where:
    @echo "warehouse: ${WAREHOUSE_PATH:-(unset - this repo's data/warehouse.duckdb)}"
    @echo "lakehouse: $LAKEHOUSE_DIR"

# DuckLake is a binary from extensions.duckdb.org that no lockfile can name.
# DuckDB would autoload it on first use; installing it here moves the download,
# and any network failure, out of a `dbt build` inside a Dagster op. DuckDB
# fetches the build for its own version, so under `uv run` it matches uv.lock.
# One-time: install runtime + dev deps into the uv-managed venv, and the DuckLake extension
setup:
    uv sync --group dev --group orchestration
    uv run python -c "import duckdb; duckdb.connect().execute('install ducklake')"

# EL: pull public sources into the DuckLake landing zone
ingest: where
    uv run python -m ingest.pipeline

# For a World Bank restatement older than the lookback window, or after the raw
# table was dropped while dlt's watermark survived.
# Re-fetch the whole WDI series instead of the incremental window
ingest-wdi-full: where
    INGEST_WDI_FULL=1 uv run python -m ingest.pipeline

# The state lives in dlt's own directory, keyed on the pipeline name (see
# `build_pipeline()`), so no warehouse query can show it. Each resource re-asks
# a lookback window behind its watermark. `just dlt-state
# modern_data_stack_fixtures` reads the fixture pipeline's.
# Show dlt's incremental state — the WDI watermark and the ECB's last fixing
dlt-state pipeline="modern_data_stack":
    uv run dlt pipeline {{ pipeline }} info -v

# The mkdir is needed because dbt's profile attaches the DuckLake catalog on
# every invocation (`dbt parse` and sqlfluff's templater included), and DuckLake
# will not create the catalog's parent directory.
# Install dbt packages (dbt_utils) into the gitignored dbt/dbt_packages/
dbt-deps:
    mkdir -p "$LAKEHOUSE_DIR"
    cd dbt && uv run dbt deps

# T: build + test dbt models
dbt-build: where dbt-deps
    cd dbt && uv run dbt build

# `@dbt_assets` reads dbt/target/manifest.json at import time and dbt/target/ is
# gitignored, so every headless `dagster` recipe depends on this. `just dagster`
# does not: `prepare_if_dev()` parses under the dev CLI.
# Write dbt/target/manifest.json — the Dagster graph won't load without it
dbt-parse: dbt-deps
    cd dbt && uv run dbt parse

# The models' parents must exist in the warehouse (schema only); run
# `just dbt-build` once if they don't.
# dbt unit tests only — mocked inputs, the inner loop for model logic
dbt-unit-test: dbt-deps
    cd dbt && uv run dbt test --select test_type:unit

# Thresholds are in models/staging/_sources.yml. `_dlt_load_id` is stamped at
# ingest, so this measures when the pipeline last ran, not when a publisher last
# published.
# Is the warehouse stale? `dbt source freshness` against dlt's load ids
dbt-freshness: dbt-deps
    cd dbt && uv run dbt source freshness

# Needs a built warehouse, or the catalog's columns come back untyped.
# Render the dbt metadata layer to dbt/target/ — columns, contracts, groups, exposures, versions, tests
dbt-docs: dbt-deps
    cd dbt && uv run dbt docs generate

# Serve the dbt docs site on :8080, regenerated first (blocks; ctrl-c to stop)
dbt-docs-serve: dbt-docs
    cd dbt && uv run dbt docs serve

# Report what the DuckLake landing zone holds — tables, rows, snapshots (read-only)
lakehouse:
    uv run python -m lake.lakehouse

# Polars derived metrics
transform: where
    uv run python -m transform.co2_intensity
    uv run python -m transform.retail_rfm

# Run after dbt-build: it reads dbt_test__audit and dbt's artifacts.
# Pipeline observability tables (load times, layer inventory, dbt test failures)
pipeline-status: where
    uv run python -m transform.pipeline_status

# Full pipeline via shell ordering (see `just materialize` for the graph-aware one)
run: ingest dbt-build transform pipeline-status

# Unit tests — mocked API payloads, no network, no warehouse
test:
    uv run pytest

# Two commands so a failing suite stops before a percentage is printed.
# Line + branch coverage of `just test` — reports, gates nothing
coverage:
    uv run coverage run -m pytest
    uv run coverage report

# The whole pipeline against checked-in fixtures, into a throwaway warehouse — what CI runs
test-pipeline:
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
    echo "fixture warehouse: $WAREHOUSE_PATH"
    uv run python -m ingest.pipeline
    cd dbt && uv run dbt deps && uv run dbt build --target-path "$DBT_TARGET_PATH" && cd ..
    uv run python -m transform.co2_intensity
    uv run python -m transform.retail_rfm
    uv run python -m transform.pipeline_status
    uv run python -m lake.lakehouse

# The exporter refuses to run without PII_SALT. A local export gets a throwaway
# salt, so its pseudonyms cannot pass for a release's; `release-data.yml` passes
# the stable repository secret (docs/DATA_PROTECTION.md says why it is stable).
# Package data/export/ for publishing: DuckDB copy, Parquet, checksums, notes
export-data:
    PII_SALT="${PII_SALT:-$(uv run python -c 'import secrets; print(secrets.token_hex(32))')}" \
        uv run python -m publish.export_warehouse

# Reads dbt/target/manifest.json (`just dbt-parse`), never the warehouse: grains
# come from the uniqueness tests, columns from the enforced contracts.
# Which conformed dimensions does each fact carry? -> docs/WAREHOUSE.md
bus-matrix:
    uv run python -m publish.bus_matrix

# Reprints the table in docs/DATA_PROTECTION.md from the warehouse. Read-only.
# How identifiable is a customer once the identifier is gone?
disclosure-risk:
    uv run python -m scripts.measure_disclosure_risk

# Copies `history` and `analytics.pipeline_runs` so the build appends to them,
# plus the landing zone when `lakehouse.tar.gz` sits beside the file (refused
# while dlt has local state). `release-data.yml` runs it before building; locally:
#   gh release download --pattern warehouse.duckdb --dir prev
#   just restore-history prev/warehouse.duckdb
# Carry a published release's unreproducible tables into this warehouse
restore-history from: where
    uv run python -m publish.restore_history {{ from }}

# Re-record the fixtures from the live APIs (hits the network; commit the diff)
record-fixtures:
    uv run python -m scripts.record_fixtures

# Dagster UI on :3000 — asset graph, run history, freshness, checks
dagster:
    mkdir -p "$DAGSTER_HOME"
    uv run --group orchestration dagster dev

# Two jobs because an asset job takes one partitions definition and retail's is
# monthly where wb_wdi's is yearly. `load_retail` first: dbt reads its table.
# See orchestration/definitions.py.
# Full pipeline ordered by the asset graph, minus the Evidence site
materialize: where dbt-parse
    mkdir -p "$DAGSTER_HOME"
    uv run --group orchestration dagster job execute -m orchestration.definitions -j load_retail
    uv run --group orchestration dagster job execute -m orchestration.definitions -j full_refresh

# What .github/workflows/pages.yml runs.
# The same graph with the Evidence site on the end of it (needs Node)
materialize-site: where dbt-parse
    mkdir -p "$DAGSTER_HOME"
    uv run --group orchestration dagster job execute -m orchestration.definitions -j load_retail
    uv run --group orchestration dagster job execute -m orchestration.definitions -j publish_site

# A bare prefix is not a glob: `marts/*` means "downstream of the key `marts/`",
# matches nothing and exits 0. Write `key:"marts/*"`; `group:`, `kind:`,
# `sinks(...)` and `roots(...)` also work. Check with `just materialize-preview`.
# Materialize a selection, e.g. `just materialize-select 'raw/wb_wdi*'` (* = all downstream, + = one layer)
materialize-select selection: where dbt-parse
    mkdir -p "$DAGSTER_HOME"
    uv run --group orchestration dagster asset materialize \
        -m orchestration.definitions --select '{{ selection }}'

# A selection matching no assets is not an error to `materialize`, so look first.
# Print the assets a selection resolves to, without materializing any of them
materialize-preview selection: dbt-parse
    uv run --group orchestration dagster asset list \
        -m orchestration.definitions --select '{{ selection }}'

# `just backfill-wdi 1995` or `just backfill-wdi 1990 1995`. Merges, so re-runs
# are idempotent. Only the raw asset is partitioned and the CLI rejects a range
# over unpartitioned ones, so follow with `just dbt-build` or `just materialize`.
# Re-load WDI for one year or a range of years
backfill-wdi start end='': where dbt-parse
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p "$DAGSTER_HOME"
    end="{{ end }}"
    uv run --group orchestration dagster asset materialize \
        -m orchestration.definitions --select 'raw/wb_wdi' \
        --partition-range "{{ start }}...${end:-{{ start }}}"

# Routine loads fetch WEATHER_COLD_START_YEARS; this deepens the archive, back to
# 1960 (the partitions' floor). Slow on purpose: the resource paces itself
# against Open-Meteo's minute, hour and day budgets, and a year of 41 capitals
# costs ~641 of the 10,000 daily units, so fifteen years is the most one run can
# hold. A longer range does not fail — it sleeps, silently, until the daily
# window drains — so split it across days. The rows are carried into the next
# release (`publish/restore_history.py`). Follow with `just dbt-build`.
# Deepen the capital-city weather archive, e.g. `just backfill-weather 2012 2026`
backfill-weather start end='': where dbt-parse
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p "$DAGSTER_HOME"
    end="{{ end }}"
    uv run --group orchestration dagster asset materialize \
        -m orchestration.definitions --select 'raw/om_weather_daily' \
        --partition-range "{{ start }}...${end:-{{ start }}}"

# Read-only unless `write`, so a session cannot change anything by accident.
# Either mode blocks a build while it is open: DuckDB allows one writer or many
# readers, never both. The lakehouse is attached in the same mode because the
# staging views read `lakehouse.raw`; without it they fail with `Catalog
# "lakehouse" does not exist!`. The CLI is the `duckdb-cli` dev dependency.
# Open the warehouse in the DuckDB CLI (`just sql write` for a writer)
sql mode="read":
    #!/usr/bin/env bash
    set -euo pipefail
    attach="install ducklake; load ducklake; attach 'ducklake:duckdb:$LAKEHOUSE_DIR/catalog.duckdb' as lakehouse (data_path '$LAKEHOUSE_DIR/data/'"
    if [ "{{ mode }}" = "write" ]; then
      uv run duckdb data/warehouse.duckdb -cmd "$attach);"
    else
      uv run duckdb -readonly data/warehouse.duckdb -cmd "$attach, read_only);"
    fi

# From dbt/, because the dbt templater resolves the profile's relative
# `../data/…` default against the working directory.
# Lint the dbt models and snapshots with sqlfluff
lint: dbt-deps
    cd dbt && uv run sqlfluff lint models snapshots

# ty is pre-1.0 and runs in neither pre-commit nor CI; `uv run` so the locked
# version answers. Suppressions go inline as `# ty: ignore[rule]`.
# Type-check the Python — reports, gates nothing
typecheck:
    uv run ty check

# The same module the `reports/evidence_site` asset calls.
# Build the Evidence dashboard (requires Node; see reports/README.md)
report:
    uv run python -m publish.build_report

# Evidence caches each source's schema and does not notice a column change, so
# use this rather than `report` after any mart or analytics column changes.
# Drop Evidence's schema cache, re-extract the sources, then build
report-clean:
    uv run python -m publish.build_report --clean

# ---------------------------------------------------------------------------
# Running as a service (docs/RUNNING_AS_A_SERVICE.md)
# ---------------------------------------------------------------------------

# `env(...)` so a deployment's EnvironmentFile wins. `publish/build_report.py`
# empties reports/build on every run, so the site is down while it rebuilds —
# §4 of the design swaps a symlink instead.
export SITE_ROOT := env("SITE_ROOT", justfile_directory() / "reports/build")

# The reasoning is §2 of docs/RUNNING_AS_A_SERVICE.md; the constraints it sets:
#   - webserver + daemon rather than `dagster dev`, so a supervisor can restart them;
#   - `dbt-parse`, because outside the dev CLI nothing writes the manifest and the
#     webserver still answers HTTP with a dead code location;
#   - `--group orchestration` on the Dagster processes (the file server carries
#     it too, harmlessly): `uv run` only ever adds packages, and it is a bare
#     `uv sync` that would strip Dagster from under a running service (§10);
#   - Dagster binds localhost (no auth); the site binds every interface (§6);
#   - `wait -n`, so one dead child ends the unit, and `kill` of the recorded
#     PIDs rather than `kill 0`, which would end this shell by SIGTERM — a clean
#     exit as far as `Restart=on-failure` is concerned.
# It does not start the `daily_refresh` schedule, which ships STOPPED (§10).
# Run the graph and the dashboard as one always-on service (blocks; ctrl-c to stop)
serve dagster_port="3000" site_port="8081": where dbt-parse
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

    uv run --group orchestration dagster-webserver -h 127.0.0.1 -p {{ dagster_port }} &
    pids+=($!)
    uv run --group orchestration dagster-daemon run &
    pids+=($!)
    uv run --group orchestration python -m http.server {{ site_port }} --directory "$SITE_ROOT" &
    pids+=($!)

    echo "dagster: http://127.0.0.1:{{ dagster_port }} (localhost)    site: http://0.0.0.0:{{ site_port }} (every interface) — $SITE_ROOT"

    status=0
    wait -n || status=$?
    echo "serve: a child process exited (status $status) — stopping the rest" >&2
    exit 1

# ---------------------------------------------------------------------------
# Course (docs/course/) — the sandbox the exercises break on purpose
# ---------------------------------------------------------------------------

# `test-pipeline` at a stable path, so a deliberately broken model is still there
# on the next command. It is the 17-country fixture slice; exercises that
# investigate the data read the real warehouse instead.
# Build the course sandbox in data/course/ (gitignored) from the fixtures
course-sandbox:
    #!/usr/bin/env bash
    set -euo pipefail
    export INGEST_FIXTURES=1
    # Absolute, or dbt (which runs from dbt/) and the Python layers (which run
    # from the repo root) resolve it to two different files.
    export WAREHOUSE_PATH="{{ justfile_directory() }}/data/course/warehouse.duckdb"
    export LAKEHOUSE_DIR="{{ justfile_directory() }}/data/course/lakehouse"
    mkdir -p "$(dirname "$WAREHOUSE_PATH")"
    rm -f "$WAREHOUSE_PATH" "$WAREHOUSE_PATH.wal"
    echo "course sandbox: $WAREHOUSE_PATH"
    uv run python -m ingest.pipeline
    cd dbt && uv run dbt deps && uv run dbt build && cd ..
    uv run python -m transform.co2_intensity
    uv run python -m transform.retail_rfm
    uv run python -m transform.pipeline_status
    echo "sandbox ready — 'just course-rebuild' after you change a model"

# No re-ingest: a broken model needs only the dbt layer rebuilt.
# The drill inner loop: rebuild the dbt layer against the sandbox
course-rebuild:
    #!/usr/bin/env bash
    set -euo pipefail
    export WAREHOUSE_PATH="{{ justfile_directory() }}/data/course/warehouse.duckdb"
    test -f "$WAREHOUSE_PATH" || { echo "no sandbox yet — run: just course-sandbox" >&2; exit 1; }
    cd dbt && uv run dbt build

# `course-rebuild` stops at dbt. A recipe rather than a command in the material,
# because the raw form forgets WAREHOUSE_PATH once and rewrites the real
# warehouse's `analytics`.
# Re-run the Polars derived metrics against the course sandbox
course-transform:
    #!/usr/bin/env bash
    set -euo pipefail
    export WAREHOUSE_PATH="{{ justfile_directory() }}/data/course/warehouse.duckdb"
    test -f "$WAREHOUSE_PATH" || { echo "no sandbox yet — run: just course-sandbox" >&2; exit 1; }
    uv run python -m transform.co2_intensity
    uv run python -m transform.retail_rfm

# One query and exit, so no open session holds the file when the next
# `course-rebuild` needs it:
#   just course-query 'select count(*) from marts.dim_country_year'
#
# It attaches the sandbox lakehouse for the reason `just sql` attaches the real
# one: `staging` is views over `lakehouse.raw`, so without it every
# `select … from staging.…` fails with `Catalog "lakehouse" does not exist!`.
# The landing tables are then `lakehouse.raw.<table>`; a bare `raw.<table>` has
# not resolved since the landing zone moved out of the DuckDB file.
# Run one read-only query against the course sandbox
course-query sql:
    #!/usr/bin/env bash
    set -euo pipefail
    export LAKEHOUSE_DIR="{{ justfile_directory() }}/data/course/lakehouse"
    uv run python -c "import duckdb, sys; \
        from lake.lakehouse import LAKEHOUSE_DIR, attach, catalog_path, data_path; \
        con = duckdb.connect('{{ justfile_directory() }}/data/course/warehouse.duckdb', read_only=True); \
        attach(con, catalog_path(LAKEHOUSE_DIR), data_path(LAKEHOUSE_DIR), alias='lakehouse', read_only=True); \
        print(con.sql(sys.argv[1]))" \
        {{ quote(sql) }}

# Everything this deletes is regenerable except data/warehouse.duckdb, whose
# `history` snapshots and `analytics.pipeline_runs` no rebuild reproduces — so
# that one is its own scope and is gated. `deep` adds reports/node_modules
# (restored by `just report`, needs Node).
# Reclaim gitignored build output (`deep` adds node_modules; `warehouse` needs --force)
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
