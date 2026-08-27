# Tests

Two tiers, deliberately separated.

## `just test` — unit tests, mocked, ~14s

`tests/test_*.py`. Every HTTP call is mocked; nothing touches the network or the
warehouse. They cover the parts of the pipeline that have actually broken:

| File | What it pins down |
|---|---|
| `test_ingest.py` | `_get_json` retry-then-raise, WDI pagination and the 200-with-an-error-body guard, the WDI incremental window (per-indicator watermarks, the full-reload escape hatch) and the replace/merge load split, the Eurostat JSON-stat stride arithmetic |
| `test_transform.py` | the Polars intensity metric — Mt→kg conversion, the constant-USD denominator, dropped rows, dense per-cohort ranking |
| `test_lake.py` | the Parquet archive: hive layout, read-back parity with the warehouse, and the stale-partition case DuckDB's `overwrite` doesn't cover |
| `test_fixtures.py` | that every URL the pipeline can build resolves to a fixture that exists |
| `test_exposures.py` | that the exposures in `dbt/models/_exposures.yml` still describe what the Evidence pages read, and that the release exposure names every mart — a stale exposure is invisible, since `dbt build` stays green and `dbt ls --select +exposure:*` keeps answering |

## `just test-pipeline` — integration against fixtures, ~30s

Runs the real `ingest → dbt build → transform` into a throwaway DuckDB file with
`INGEST_FIXTURES=1`, so dlt's schema inference, both of its load calls (replace
then merge), every dbt model, seed, snapshot and test, and the Polars layer all
execute — offline and deterministically. This is what `.github/workflows/ci.yml`
runs (via the Dagster asset graph, so the asset checks are evaluated too).

It sets `WAREHOUSE_PATH` to a temp file, and `LAKE_DIR` to a temp directory
beside it. Don't drop either: without them a fixture run overwrites
`data/warehouse.duckdb` and `data/lakehouse/` with the 17-country slice.

## `just coverage` — line and branch coverage of the first tier, ~17s

`coverage run -m pytest`, configured in `pyproject.toml`. It reports and gates
nothing: there is no `fail_under`, nothing in CI runs it, and pytest is run
*under* coverage rather than loading a plugin, so there is no flag to leave
switched on by accident. Same shape as `just typecheck`.

**`pytest-cov` was tried first and dropped**, because it measured identically —
same total, same runtime to within 0.02s — for one more package. The wrapper was
buying a `--cov` flag; the config it reads is coverage.py's either way.

**Two caveats, or the total misleads.** It measures *this* tier only, so the
transform and lake layers read low while `just test-pipeline` exercises them end
to end — understated, not untested. And some of what is uncovered is uncovered
on purpose: `scripts/record_fixtures.py` sits near 30% because nothing checks the
recorder against the routes deliberately (it writes through `path_for()`, so a
test would assert what the code makes impossible). It is reported rather than
`omit`ted, because hiding a deliberate gap is how it stops being a decision.

Branch coverage is on. This project carries at least four branches that are
deliberately unreachable and argued for in prose; branch coverage is what makes
them a number rather than a paragraph.

## `tests/fixtures/ingest/` — the recorded payloads

Produced by `just record-fixtures` (`scripts/record_fixtures.py`), which hits the
five live endpoints and trims each to those 17 countries — chosen to cover every
World Bank region and income group, both Eurostat geo-code exceptions (`EL`, `UK`),
and Taiwan, which the World Bank omits and the `country_overrides` seed exists for.

Rows are filtered; **columns never are**. Dropping unused columns would let a
renamed upstream field pass CI against a fixture that agrees with a `stg_` model
no longer matching reality. The OWID fixtures stay gzipped CSV instead of Parquet
for the same reason: they still go through
`pl.read_csv(..., infer_schema_length=None)`, so the inference gotcha is under
test and not bypassed.

Re-record when a source changes shape or a WDI indicator is added, and commit the
result. `.github/workflows/nightly.yml` is what tells you it's time: it runs the
same graph against the live endpoints daily and opens an issue when they've moved.
