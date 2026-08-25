# Lightweight orchestration. `just <recipe>`; run `just` to list.
# (Install: `uv tool install rust-just` or use your package manager.)

set dotenv-load := true

# Dagster keeps its run/event storage here (gitignored except dagster.yaml).
export DAGSTER_HOME := justfile_directory() / ".dagster"

default:
    @just --list

# One-time: install runtime + dev deps into the uv-managed venv
setup:
    uv sync --group dev --group orchestration

# EL: pull public sources into DuckDB
ingest:
    uv run python -m ingest.pipeline

# Same, but re-fetch the whole WDI series instead of the incremental window.
# For a World Bank restatement older than the 5-year lookback (or to rebuild
# history after the raw table was dropped out from under the watermark).
ingest-wdi-full:
    INGEST_WDI_FULL=1 uv run python -m ingest.pipeline

# What the next incremental run will ask for. dlt keeps this in ~/.dlt/pipelines,
# keyed on the pipeline *name* and not the destination, so it is not in this repo
# and no query against the warehouse can show it: WDI's `max_year_by_indicator`
# (one entry per indicator, so a newly added code still pulls its whole series)
# and the ECB's `max_rate_date`. Neither is the fetch floor — `wdi_start_year()`
# subtracts WDI_LOOKBACK_YEARS from the watermark, because the World Bank revises
# what it has already published.
#
# `just dlt-state modern_data_stack_fixtures` reads the fixture pipeline, which
# carries the `_fixtures` suffix precisely so a fixture run cannot move the real
# watermark. Needs at least one `just ingest` to have happened.
# (`just --list` renders only the line directly above a recipe.)
# Show dlt's incremental state — the WDI watermark and the ECB's last fixing
dlt-state pipeline="modern_data_stack":
    uv run dlt pipeline {{ pipeline }} info -v

# Install dbt packages (dbt_utils) into dbt/dbt_packages/ — gitignored, so this
# is needed once per clone and after any packages.yml change
dbt-deps:
    cd dbt && uv run dbt deps

# T: build + test dbt models
dbt-build: dbt-deps
    cd dbt && uv run dbt build

# Unit tests only — mocked inputs, asserted outputs, no warehouse read. The inner
# loop when changing model *logic*: `dbt build` runs these too, but this is ~2s
# against a full build. Parents must exist in the warehouse (schema only is
# enough); `just dbt-build` once if they don't.
dbt-unit-test: dbt-deps
    cd dbt && uv run dbt test --select test_type:unit

# Is the warehouse stale? Compares dlt's `_dlt_load_id` against the thresholds
# in models/staging/_sources.yml (warn at 7 days, error at 30). This measures
# when the pipeline last ran, not when the publishers last updated.
dbt-freshness: dbt-deps
    cd dbt && uv run dbt source freshness

# The catalog is read out of the warehouse, so run `just dbt-build` first or the
# columns come back with no types. ~7s. Output is gitignored; `just clean` drops
# it with the rest of dbt/target. Nothing else here displays this layer — the
# Dagster UI shows the asset graph and check results, Evidence shows the data.
# (`just --list` renders only the line directly above a recipe, so the one-line
# summary goes last, not first.)
# Render the dbt metadata layer to dbt/target/ — columns, contracts, groups, exposures, versions, tests
dbt-docs: dbt-deps
    cd dbt && uv run dbt docs generate

# Regenerates first, so what you are reading is never behind the models.
# Serve the dbt docs site on :8080 (blocks; ctrl-c to stop)
dbt-docs-serve: dbt-docs
    cd dbt && uv run dbt docs serve

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

# A bare prefix is NOT a glob: `marts/*` reads as "everything downstream of the
# key `marts/`", so it matches nothing and exits 0. Glob a key prefix with
# `key:"marts/*"`; `group:`, `kind:`, `sinks(...)` and `roots(...)` work too.
# See what a selection resolves to before materializing it, with
# `dagster asset list -m orchestration.definitions --select '<sel>'`.
# (`just --list` shows only the line below, so keep the summary last.)
# Materialize a selection, e.g. `just materialize-select 'raw/wb_wdi*'` (* = all downstream, + = one layer)
materialize-select selection:
    mkdir -p "$DAGSTER_HOME"
    uv run --group orchestration dagster asset materialize \
        -m orchestration.definitions --select '{{ selection }}'

# The read-only half of the recipe above: resolves a selection and prints the
# asset keys it matches, running nothing. Worth reaching for first, because a
# selection that matches *nothing* is not an error — `dagster asset materialize`
# exits 0 having done nothing at all. A bare prefix is the way to get one:
# `marts/*` reads as "downstream of the key `marts/`" and matches none, where
# `key:"marts/*"` matches the seventeen marts. `group:`, `kind:`, `sinks(...)`
# and `roots(...)` work here and there alike.
# (`just --list` renders only the line directly above a recipe.)
# Print the assets a selection resolves to, without materializing any of them
materialize-preview selection:
    uv run --group orchestration dagster asset list \
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

# Read-only is the default because DuckDB takes a single writer: a session
# holding the write lock makes `just run` fail with a lock error two terminals
# away. Pass `just sql write` when you actually mean to write.
#
# The CLI is a separate install from the Python package — `duckdb` on PyPI
# ships no console script — so this checks for it rather than failing with a
# bare 'command not found'. Note that `just --list` shows only the LAST
# comment line above a recipe, so that line has to be the summary.
#
# Open the warehouse in the DuckDB CLI (`just sql write` for a writer)
sql mode="read":
    #!/usr/bin/env bash
    set -euo pipefail
    if ! command -v duckdb >/dev/null 2>&1; then
      echo "The DuckDB CLI isn't installed. Get it with:" >&2
      echo "  curl https://install.duckdb.org | sh" >&2
      echo "(the 'duckdb' PyPI package is the Python library only)" >&2
      exit 1
    fi
    if [ "{{ mode }}" = "write" ]; then
      duckdb data/warehouse.duckdb
    else
      duckdb -readonly data/warehouse.duckdb
    fi

# Lint SQL — from dbt/, because the dbt templater opens the warehouse via the
# profile's relative path (`../data/…`) without chdir'ing into the project first
lint: dbt-deps
    cd dbt && uv run sqlfluff lint models snapshots

# ty is pre-1.0 and nothing in pre-commit or CI runs it, so a version bump can
# move the count without turning a workflow red. Suppressions go inline as
# `# ty: ignore[rule]` at the decision rather than into a rules list; [tool.ty]
# in pyproject.toml overrides nothing. `uv run`, not a global `ty`, so the
# locked version is the one that answers — the sqlfluff single-pin rule.
# Type-check the Python — reports, gates nothing
typecheck:
    uv run ty check

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

# Reclaim gitignored build output. Every target below is produced by a recipe in
# this file, so deleting it costs rebuild time and nothing else — with exactly
# one exception, which is why this recipe takes a scope instead of just running.
#
# The exception is `data/warehouse.duckdb`. It holds the `history` schema: two
# dbt snapshots that accumulate one row per revision and that NO rebuild can
# reproduce, because they are a record of what the sources said on the days we
# asked. Delete the file and those versions are gone for good — `just
# restore-history` can only recover what a published release happened to carry.
#
# `just clean` reclaims the safe tier; `just clean deep` also takes
# reports/node_modules (931 MB, restored by `just report`, needs Node).
#
# Reclaim gitignored build output (`deep` adds node_modules; `warehouse` needs --force)
clean scope="safe" force="":
    #!/usr/bin/env bash
    set -euo pipefail
    cd "{{ justfile_directory() }}"

    # `just clean warehouse [--force]` — the one target that is not derived.
    #
    # The gate runs BEFORE anything is deleted. A refused `just clean warehouse`
    # used to still take the whole safe tier with it on the way to saying no,
    # which is a poor thing for a command that refused to do.
    #
    # Mirrors `scripts/restore_history.py`: the gate is not "is this scary", it
    # is "is there history here to lose". An empty `history` schema (a fresh
    # clone, a warehouse built but never snapshotted) has nothing a rebuild
    # cannot reproduce, so it goes without ceremony. Rows there stop it until
    # `--force`, and the message names the count — the same shape, and the same
    # sentence, as the refusal in `modern_data_stack.history`.
    #
    # Summed over every table in the schema, not over a named snapshot: there
    # are two of them and anything naming one has to name both.
    if [ "{{ scope }}" = "warehouse" ]; then
      if [ ! -e data/warehouse.duckdb ]; then
        echo "  data/warehouse.duckdb is already gone"
      else
        history_rows='import duckdb; from modern_data_stack.db import scalar; con = duckdb.connect("data/warehouse.duckdb", read_only=True); q = "select table_name from information_schema.tables where table_schema = ?"; tables = [r[0] for r in con.execute(q, ["history"]).fetchall()]; print(sum(scalar(con, f"select count(*) from history.{t}") for t in tables))'
        held=$(uv run python -c "$history_rows") || held=""
        # An unreadable count must refuse, not fall through. `[ "" -gt 0 ]` is an
        # error, but inside an `if` that reads as *false* and `set -e` does not
        # fire — so a warehouse whose history could not be counted would have
        # been deleted by the safe-looking branch. Fail closed instead.
        #
        # `--force` deliberately does not override this one. The check cannot
        # tell a corrupt file from one a running Dagster job holds the lock on,
        # and deleting the second is the worse mistake. `rm` by hand is the
        # escape hatch, and it is the right amount of friction.
        case "$held" in
          ''|*[!0-9]*)
            echo "could not read the history row count from data/warehouse.duckdb" >&2
            echo "(locked by another process?) — refusing to delete it" >&2
            exit 1
            ;;
        esac
        if [ "$held" -gt 0 ] && [ "{{ force }}" != "--force" ]; then
          echo "data/warehouse.duckdb holds $held rows of snapshot history — refusing" >&2
          echo "to delete history that a rebuild cannot reproduce. Pass --force if" >&2
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

    # Regenerable with no state in them at all.
    #   dbt/target        `dbt parse` / `just dbt-build`   (the manifest)
    #   dbt/dbt_packages  `just dbt-deps`
    #   data/lake         `just lake`
    #   data/export       `just export-data`
    #   data/course       `just course-sandbox`
    #   data/cache        re-downloaded on the next ingest
    #   reports/build     `just report`
    #   reports/.evidence `just report-clean`
    drop dbt/target dbt/dbt_packages dbt/logs \
         data/lake data/export data/course data/cache \
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
