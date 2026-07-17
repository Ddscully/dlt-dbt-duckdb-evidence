# CLAUDE.md

Guidance for Claude Code (and humans) working in this repo.

## What this is

A public demo of a modern, lightweight data-engineering + BI stack. Everything
runs locally with `uv` against a single DuckDB file — no cloud warehouse.

```
dlt (EL) → DuckDB → dbt (staging/marts) → Polars (heavy T) → Evidence (BI)
```

## Commands

Use the `justfile` recipes (they map to plain `uv run …` commands):

| Command | What it does |
|---------|--------------|
| `just setup` | `uv sync --group dev --group notebook` |
| `just ingest` | run the dlt pipeline → `raw` schema in DuckDB |
| `just dbt-build` | `cd dbt && uv run dbt build` (models + tests) |
| `just transform` | Polars derived metrics → `analytics` schema |
| `just run` | ingest → dbt-build → transform (full pipeline) |
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
  wrong type, re-running won't fix it — the pipeline uses `refresh="drop_sources"`
  to force re-inference each run. Don't remove that without a reason.
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
