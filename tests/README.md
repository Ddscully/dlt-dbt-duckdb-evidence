# Tests

Two tiers, deliberately separated.

## `just test` — unit tests, mocked, ~1s

`tests/test_*.py`. Every HTTP call is mocked; nothing touches the network or the
warehouse. They cover the parts of the pipeline that have actually broken:

| File | What it pins down |
|---|---|
| `test_ingest.py` | `_get_json` retry-then-raise, WDI pagination and the 200-with-an-error-body guard, the WDI incremental window (per-indicator watermarks, the full-reload escape hatch) and the replace/merge load split, the Eurostat JSON-stat stride arithmetic |
| `test_transform.py` | the Polars intensity metric — Mt→kg conversion, the constant-USD denominator, dropped rows, dense per-cohort ranking |
| `test_fixtures.py` | that every URL the pipeline can build resolves to a fixture that exists |

## `just test-pipeline` — integration against fixtures, ~30s

Runs the real `ingest → dbt build → transform` into a throwaway DuckDB file with
`INGEST_FIXTURES=1`, so dlt's schema inference, both of its load calls (replace
then merge), every dbt model, seed, snapshot and test, and the Polars layer all
execute — offline and deterministically. This is what `.github/workflows/ci.yml`
runs (via the Dagster asset graph, so the asset checks are evaluated too).

It sets `WAREHOUSE_PATH` to a temp file. Don't drop that: without it a fixture
run overwrites `data/warehouse.duckdb` with the 17-country slice.

## `tests/fixtures/ingest/` — the recorded payloads

Produced by `just record-fixtures` (`scripts/record_fixtures.py`), which hits the
five live endpoints and trims each to those 17 countries — chosen to cover every
World Bank region and income group, both Eurostat geo-code exceptions (`EL`, `UK`),
and Taiwan, which the World Bank omits and the `country_overrides` seed exists for.

Rows are filtered; **columns never are**. Dropping unused columns would let a
renamed upstream field pass CI against a fixture that agrees with a `stg_` model
no longer matching reality. The OWID fixtures stay gzipped CSV rather than
Parquet for the same reason — they still go through
`pl.read_csv(..., infer_schema_length=None)`, so the inference gotcha is under
test rather than bypassed.

Re-record when a source changes shape or a WDI indicator is added, and commit the
result. `.github/workflows/nightly.yml` is what tells you it's time: it runs the
same graph against the live endpoints daily and opens an issue when they've moved.
