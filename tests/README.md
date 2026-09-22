# Tests

Two tiers, deliberately separated.

## `just test` — pytest over mocked payloads, ~47 s

`tests/test_*.py`. Every HTTP call is mocked; nothing touches the network or the
warehouse. These five pin the pipeline's own logic, where it has actually broken:

| File | What it pins down |
|---|---|
| `test_ingest.py` | `http.get_json`'s retry-then-raise, WDI pagination and the 200-with-an-error-body guard, the WDI incremental window (per-indicator watermarks, the full-reload escape hatch) and the replace/merge load split, the Eurostat JSON-stat stride arithmetic |
| `test_transform.py` | the Polars intensity metric — Mt→kg conversion, the constant-USD denominator, dropped rows, dense per-cohort ranking |
| `test_lakehouse.py` | the DuckLake landing zone: that two snapshots diff to the revisions dlt's merge hides, that `revisions()` refuses to answer rather than compare nothing, and that three hand-maintained lists still match dlt — the provenance columns, the weather table's name and the ATTACH alias dbt declares |
| `test_fixtures.py` | that every URL the pipeline can build resolves to a fixture that exists |
| `test_exposures.py` | that the exposures in `dbt/models/_exposures.yml` still describe what the Evidence pages read, and that the release exposure names every mart — a stale exposure is invisible, since `dbt build` stays green and `dbt ls --select +exposure:*` keeps answering |

The rest group by what they protect:

| Protects | Files |
|---|---|
| hand-maintained lists against the tree | `test_definitions`, `test_asset_checks`, `test_workflows`, `test_documented_counts`, `test_course`, `test_agent_instructions`, `test_plugin_settings`, `test_packaging` |
| the model metadata | `test_additivity`, `test_bus_matrix` |
| the publication boundary | `test_export`, `test_privacy`, `test_restore_history` |
| the package's mechanisms | `test_paths`, `test_db`, `test_workbook`, `test_ratelimit`, `test_pipeline_status`, `test_report` |
| the service | `test_dagster_instance`, `test_docker_launcher` |

Which list each guard holds, and the traps in writing one, are the `repo-guards`
skill.

## `just test-pipeline` — integration against fixtures, ~46 s

Runs the real `ingest → dbt build → transform` into a throwaway DuckDB file with
`INGEST_FIXTURES=1`, so dlt's schema inference, both of its load calls (replace
then merge), every dbt model, seed, snapshot and test, and the Polars layer all
execute — offline and deterministically. This is what `.github/workflows/ci.yml`
runs (via the Dagster asset graph, so the asset checks are evaluated too).

It points `WAREHOUSE_PATH`, `LAKEHOUSE_DIR` and dbt's artifact paths into a temp
directory (and, when set, gives a bucket or a Postgres catalog a prefix or schema
of its own). Don't drop any of them: without them a fixture run overwrites
`data/warehouse.duckdb` and `data/lakehouse/` with the 17-country slice, and
files its dbt results into the real run history. The landing zone matters most,
because dlt *lands* there, over a weather archive no rebuild can afford to
refetch.

## `just coverage` — line and branch coverage of the first tier, ~58 s

`coverage run -m pytest`, configured in `pyproject.toml`. It reports and gates
nothing: there is no `fail_under`, nothing in CI runs it, and pytest is run
*under* coverage rather than loading a plugin, so there is no flag to leave
switched on by accident. Same shape as `just typecheck`.

Not `pytest-cov`, which measured identically for one more package
([decision 0003](../docs/decisions/0003-coverage-py-over-pytest-cov.md)).

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

**`[tool.coverage.run] source` is a hand-maintained directory list with no
guard.** `publish/` was missing from it, and adding it moved the totals *up*,
because the blind spot hid well-covered code. `COVERAGE_CORE=sysmon` saves
nothing here: the cost is imports and DuckDB/dlt work, not line tracing.

## `tests/fixtures/ingest/` — the recorded payloads

Produced by `just record-fixtures` (`scripts/record_fixtures.py`), which hits
every live endpoint and trims each to a slice. The country-year sources keep 17
countries, chosen to cover every World Bank region and income group, both
Eurostat geo-code exceptions (`EL`, `UK`), and Taiwan, which the World Bank omits
and the `country_overrides` seed exists for. The others are trimmed on their own
terms: whole invoices for retail, three years of all 41 capitals for weather, and
the FX series untrimmed. Each recorder's docstring says why.

Rows are filtered; **columns never are**. Dropping unused columns would let a
renamed upstream field pass CI against a fixture that agrees with a `stg_` model
no longer matching reality. The OWID fixtures stay gzipped CSV instead of Parquet
for the same reason: they still go through
`pl.read_csv(..., infer_schema_length=None)`, so the inference gotcha is under
test and not bypassed.

Re-record when a source changes shape or a WDI indicator is added, and commit the
result. `.github/workflows/nightly.yml` is what tells you it is time: it runs the
same graph against the live endpoints daily and opens an issue when they have moved.
