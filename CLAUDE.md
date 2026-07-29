# CLAUDE.md

Guidance for Claude Code (and humans) working in this repo.

## What this is

A public demo of a modern, lightweight data-engineering + BI stack. Everything
runs locally with `uv` against a single DuckDB file — no cloud warehouse.

```
dlt (EL) → DuckDB → dbt (staging/marts) → Polars (heavy T) → Evidence (BI)
                    all orchestrated by Dagster
```

## Commands

Use the `justfile` recipes (they map to plain `uv run …` commands):

| Command | What it does |
|---------|--------------|
| `just setup` | `uv sync --group dev --group notebook --group orchestration` |
| `just ingest` | run the dlt pipeline → `raw` schema in DuckDB |
| `just dbt-build` | `cd dbt && uv run dbt build` (models + tests) |
| `just transform` | Polars derived metrics → `analytics` schema |
| `just run` | ingest → dbt-build → transform (shell ordering) |
| `just dagster` | Dagster UI on :3000 — asset graph, runs, freshness, checks |
| `just materialize` | same pipeline, ordered by the asset graph |
| `just materialize-select 'raw/wb_wdi+'` | one asset + everything downstream |
| `just lint` | `sqlfluff lint dbt/models` |
| `just sql` | open the warehouse in Harlequin |
| `just notebook` | marimo exploration notebook |

Always run tools through `uv run` so they use the project venv. dbt commands
must run from the `dbt/` directory (that's where `profiles.yml` lives).

## Warehouse schemas (one DuckDB file: `data/warehouse.duckdb`)

- `raw` — dlt landing tables: `owid_co2`, `owid_energy`, `wb_country`, `wb_wdi`,
  `eu_elec_prices`
- `staging` — dbt views, `stg_*`, cleaned to `(country_iso3, year)` grain
- `marts` — dbt tables, `fct_emissions_energy` (the wide join)
- `analytics` — Polars output, `co2_intensity`

Grain of every fact/staging model is **`(country_iso3, year)`**; joins are on
ISO3 country code + year. The country dimension (`stg_country`) supplies
`region` and `income_group`.

## Conventions & gotchas (learned the hard way)

- **Clean schema names** come from `dbt/macros/generate_schema_name.sql`, which
  overrides dbt's default `<target>_<custom>` (which would give `main_marts`).
  Reference marts as `marts.fct_emissions_energy`, not `main_marts.…`.
- **dlt persists its schema and only *widens* types.** If a column lands with the
  wrong type, re-running won't fix it — the pipeline uses
  `refresh="drop_resources"` (`REFRESH` in `ingest/pipeline.py`) to force
  re-inference each run. Don't remove that without a reason. It's
  `drop_resources` and not `drop_sources` because Dagster can run a subset of the
  source: `drop_sources` would wipe the four tables that *weren't* selected.
- **Polars CSV type inference** defaults to the first 100 rows. OWID's early rows
  are empty for most metrics, so `pl.read_csv(..., infer_schema_length=None)` is
  required or numeric columns land as VARCHAR.
- **World Bank JSON is snake_cased by dlt**: API `iso2Code`/`capitalCity`/
  `incomeLevel.value` land as `iso2_code`/`capital_city`/`income_level__value`.
  Verify column names against `information_schema.columns` before writing SQL.
- **World Bank WDI** is fetched long (one row per indicator/country/year) and
  pivoted to wide columns in `stg_wdi.sql`. Add indicators in two places:
  `WB_WDI_INDICATORS` in `ingest/pipeline.py` and a `max(case …)` in `stg_wdi.sql`.
- **Eurostat is JSON-stat** — a flat `value` dict keyed by a row-major index over
  all dimensions. `eu_elec_prices` filters every dimension but `geo`/`time`
  server-side, then walks that grid (see `pipeline.py`). Its `geo` codes are ISO2
  *except* `EL`=Greece (GR) and `UK`=UK (GB); `stg_eu_electricity_prices.sql`
  remaps those, joins `stg_country` for ISO3, and averages the two half-years to
  annual. EU/EEA only, so the mart column is null for the rest of the world.

## Orchestration (`orchestration/`)

Dagster wraps the existing layers; it doesn't replace them. `ingest`, `dbt` and
`transform` stay independently runnable, and `orchestration/assets.py` imports
them rather than duplicating logic (`build_pipeline()`, `dbt build`,
`transform.co2_intensity.run()`).

- **Asset keys are the join between the layers.** dlt resources are keyed
  `raw/<resource>` by `RawSchemaDltTranslator` specifically to match the keys
  dagster-dbt derives from `_sources.yml`. Rename a dbt source table without
  renaming the dlt resource and the graph silently splits in two — the halves
  still run, just unconnected. Check with `dagster definitions validate` plus a
  look at the graph.
- **`orchestration/assets.py` must not use `from __future__ import annotations`.**
  Dagster inspects the `context` parameter's annotation *object*; a stringified
  annotation fails its check with a confusing "Cannot annotate `context`" error.
- **Everything runs in one process** (`in_process_executor`, and all five dlt
  resources in a single op). DuckDB takes one writer at a time, so parallel steps
  would just fight over the file lock.
- The `daily_refresh` schedule ships `STOPPED` on purpose — opening the UI
  shouldn't start hammering public APIs on a timer.
- Dagster state lives in `.dagster/` (`DAGSTER_HOME`, exported by the justfile).
  Only `dagster.yaml` is checked in.

## Verifying changes

After changing ingestion or models, run the real pipeline (`just run`) and
inspect the warehouse — don't assume. Quick check:

```bash
uv run python -c "import duckdb; \
  print(duckdb.connect('data/warehouse.duckdb', read_only=True).sql(\
  'select * from marts.fct_emissions_energy limit 5').df())"
```

## Session history

Exported Claude Code session logs live in `docs/sessions/` — see the README
there. They're checked into git as a running project history.
