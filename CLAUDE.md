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
| `just materialize-select 'raw/wb_wdi*'` | one asset + everything downstream (`*` all, `+` one layer) |
| `just test` | `pytest` — mocked-payload unit tests, no network |
| `just test-pipeline` | the whole pipeline against fixtures, into a throwaway warehouse |
| `just record-fixtures` | re-record `tests/fixtures/ingest/` from the live APIs |
| `just lint` | `sqlfluff lint dbt/models` |
| `just sql` | open the warehouse in Harlequin |
| `just notebook` | marimo exploration notebook |

Always run tools through `uv run` so they use the project venv. dbt commands
must run from the `dbt/` directory (that's where `profiles.yml` lives).

## Style guide

SQL and model conventions live in [`docs/STYLE_GUIDE.md`](docs/STYLE_GUIDE.md) —
naming, grain, import CTEs, column ordering, and where this project deliberately
departs from [dbt Labs' style guide](https://docs.getdbt.com/best-practices/how-we-style/0-how-we-style-our-dbt-projects).
The formatting half of it is enforced by [`.sqlfluff`](.sqlfluff); run
`just lint` (pre-commit runs the same check).

## Agent skills

Vendor skills for each layer are declared in [`.claude/settings.json`](.claude/settings.json),
so Claude Code offers to install them when you trust this repo. They carry the
tool-level knowledge; this file and the project skills carry the repo-level
knowledge.

| Plugin | Covers |
|--------|--------|
| `dbt@dbt-agent-marketplace` | [dbt Labs' skills](https://github.com/dbt-labs/dbt-agent-skills) — models, tests, docs, debugging |
| `dagster-expert@dagster` | [Dagster's skills](https://github.com/dagster-io/skills) — assets, automation, CLI |
| `polars@polars` | [Polars' skill](https://github.com/polars-inc/skills) — idiomatic lazy-API Polars |
| `duckdb-skills@duckdb-skills` | [DuckDB's skills](https://github.com/duckdb/duckdb-skills) — querying, file formats, docs search |

Not enabled, but worth knowing about: `dbt-migration@dbt-agent-marketplace`
(one-off dbt Core → Fusion work), `dignified-python@dagster`, and dltHub's
[AI Workbench](https://github.com/dlt-hub/dlthub-ai-workbench)
(`/plugin marketplace add dlt-hub/dlthub-ai-workbench`) — the workbench assumes
its own scaffolding, so prefer the `adding-a-data-source` skill below for the
pipeline that already exists here.

Project skills in `.claude/skills/` cover the seams the vendor skills can't know:

- **`adding-a-data-source`** — the cross-layer workflow (dlt resource → dbt
  source → staging → mart → Dagster asset key → Evidence), including the
  name-matching that silently splits the asset graph if you get it wrong.
- **`querying-the-warehouse`** — read-only connections, the single-writer lock,
  clean schema names, checking `raw` column names before writing SQL.
- **`building-evidence-reports`** — the Evidence layer, which has no vendor skill.

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
- **The World Bank doesn't list every ISO3 OWID emits for.** Taiwan (~286 Mt CO2,
  bigger than the Netherlands) and ten small territories arrive with a null
  `region`, so any `where region is not null` silently drops them from regional
  rollups. `dbt/seeds/country_overrides.csv` fills them in and `stg_country`
  unions it in. Antarctica is deliberately left out — a null `region` should mean
  "not a country". Coordinates use `try_cast`: the API sends `''` for territories.
- **World Bank region names are padded** — `'Sub-Saharan Africa '` and
  `'Latin America & Caribbean '` come back with a trailing space. `stg_country`
  trims them, so join and group on the trimmed values.
- **Two carbon-intensity columns, different bases.** `fct_emissions_energy.co2_per_gdp`
  is OWID's kg CO2 per 2011 international-$ (PPP) and stops in 2022 / 164
  countries. `analytics.co2_intensity.co2_per_gdp_const_usd` is derived in
  `transform/co2_intensity.py` and tracks the mart — ~197 countries through 2024,
  but only back to 1960, where WDI starts. Levels aren't comparable between the
  two; the rank uses only the derived one.
- **Divide by `gdp_constant_usd`, never `gdp_usd`, for anything measured over
  time.** `gdp_usd` (`NY.GDP.MKTP.CD`) is *current* US$, so it moves with
  inflation and the exchange rate: on that basis Japan cut emissions 21% from
  2010–2024 and still scored 10% *worse* on carbon intensity, purely because the
  yen fell 28% against the dollar. `gdp_constant_usd` (`NY.GDP.MKTP.KD`, constant
  2015 US$) is the real-terms series. Current US$ is fine for single-year
  cross-sections, wrong for trends.
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

## Testing (`tests/`)

Two tiers, and the split is the point — see [`tests/README.md`](tests/README.md).

- `just test` — mocked-payload unit tests over the ingest/transform logic. No
  network, no warehouse, ~1s.
- `just test-pipeline` — the real modules end to end with `INGEST_FIXTURES=1`,
  serving all five sources from `tests/fixtures/ingest/`. This is what CI runs,
  so a red PR build means the repo broke, not that OWID was down.

Gotchas:

- **`WAREHOUSE_PATH` overrides the DuckDB file** for `ingest`, `transform` *and*
  dbt's profile. It must be **absolute**: dbt resolves its path from `dbt/`, the
  Python layers from the repo root. `just test-pipeline` sets it to a temp file —
  without that, a fixture run overwrites the real warehouse with the 17-country
  slice.
- **Fixtures filter rows, never columns.** Column-trimming would let a renamed
  upstream field pass CI against a fixture that matches a `stg_` model no longer
  matching reality. The OWID fixtures are gzipped CSV, not Parquet, so they still
  go through `pl.read_csv(..., infer_schema_length=None)`.
- **`fixtures.path_for()` raises on an unmapped URL** rather than falling back to
  the network — otherwise "offline CI" quietly becomes "CI that's online
  sometimes". `tests/test_fixtures.py` asserts every URL the pipeline can build
  resolves to a file that exists.
- **dlt wraps anything a resource generator raises** in `ResourceExtractionError`,
  so tests asserting on ingest errors match that, not the underlying exception.
- **Adding a WDI indicator means re-recording** (`just record-fixtures`), on top
  of the two places listed above.
- `.github/workflows/nightly.yml` runs the same graph against the *live* sources
  daily and opens (or comments on) a `nightly-failure` issue. That's the signal
  that the fixtures have drifted from reality.

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
