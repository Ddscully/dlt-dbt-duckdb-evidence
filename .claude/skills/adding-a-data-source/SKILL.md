---
name: adding-a-data-source
description: End-to-end workflow for adding a new public data source to this warehouse — dlt resource, dbt source + staging model, mart column, Dagster asset key, and the report. Use whenever adding, renaming, or removing a source, a dlt resource, a WDI indicator, or a raw table.
---

# Adding a data source

Adding a source touches five layers that are wired together by **name**, not by
imports. Miss one and the pipeline still runs — it just quietly splits into two
disconnected halves. Work the checklist top to bottom.

Vendor skills cover the *how* of each tool (`dbt`, `dagster-expert`, `polars`,
`duckdb-skills`). This skill covers the *seams between them*, which are specific
to this repo. Naming rules live in [`docs/STYLE_GUIDE.md`](../../../docs/STYLE_GUIDE.md).

## 0. First decide whether you need a new source at all

If the data is another World Bank indicator, you do **not** need a new resource —
see "Adding a WDI indicator" at the bottom. That's a two-line change.

## 1. dlt resource — `ingest/pipeline.py`

Add a `@dlt.resource(name="<resource>", write_disposition="replace")` function,
register it in the `public_indicators()` source, **and add its name to
`FULL_REFRESH_RESOURCES`** (or `INCREMENTAL_RESOURCES` if it merges — see below).
`load_groups()` loads only what those tuples name, so a resource missing from
both is extracted by nothing; `tests/test_ingest.py` asserts they cover the
source exactly.

- The `name=` you pick becomes the raw table name **and** the second element of
  the Dagster asset key (`raw/<resource>`). Choose it once and don't rename it
  casually — step 4 explains what breaks.
- Fetch through `_get_json()` so you inherit its retry + `raise_for_status`
  behaviour. Don't hand-roll a `requests.get`.
- **CSV sources:** `pl.read_csv(..., infer_schema_length=None)`. The default
  100-row inference sees OWID's empty early rows and lands numerics as VARCHAR.
- **Leave `REFRESH = "drop_resources"` alone.** dlt persists its schema and only
  *widens* types, so a column that lands wrong is not fixed by re-running. It's
  `drop_resources` and not `drop_sources` because Dagster can materialize a
  subset — `drop_sources` would wipe the tables that weren't selected.
- **`replace` is the default answer.** These sources are small and a full reload
  keeps the schema honest. Reach for `merge` only when the pull is genuinely
  expensive, and then copy `wb_wdi` wholesale: a primary key that really is the
  grain, declared `columns={...}` types (the schema is no longer re-inferred, so
  inference can't save you), a *lookback window* rather than a high-water mark if
  the publisher restates, and the name in `INCREMENTAL_RESOURCES` so it loads in
  the un-refreshed call.

Run `just ingest` and look at the real column names before writing any SQL:

```bash
uv run python -c "import duckdb; \
  print(duckdb.connect('data/warehouse.duckdb', read_only=True).sql(\
  \"select column_name, data_type from information_schema.columns \
    where table_schema='raw' and table_name='<resource>'\").df())"
```

dlt snake_cases and flattens nested JSON: `iso2Code` → `iso2_code`,
`incomeLevel.value` → `income_level__value`. Never guess these.

## 2. dbt source — `dbt/models/staging/_sources.yml`

Add the table under the `raw` source. **The `name:` here must equal the dlt
resource name from step 1.**

## 3. Staging model — `dbt/models/staging/stg_<thing>.sql`

Cleans to the project grain, `(country_iso3, year)`:

- Rename the source's key columns to `country_iso3` and `year`. Every model in
  the warehouse joins on those two and nothing else.
- Drop non-country aggregates. The `stg_co2` pattern is
  `where iso_code is not null and length(iso_code) = 3`.
- If the source is keyed by ISO2, join `stg_country` to get ISO3. Watch for
  non-standard codes — Eurostat sends `EL` for Greece and `UK` for the UK, and
  `stg_eu_electricity_prices.sql` remaps both.
- Import CTEs at the top, one `{{ source() }}` or `{{ ref() }}` each.
- Add the model to the staging YAML with a description that states the grain and
  any partial coverage.

Run `just lint` — the style rules are enforced, not advisory.

## 4. Dagster asset key — usually nothing to do, but verify

`RawSchemaDltTranslator` in `orchestration/assets.py` keys every dlt resource as
`raw/<resource>`, which is exactly the key `dagster-dbt` derives from
`_sources.yml`. That matching string is the *only* thing joining the EL half of
the graph to the T half.

So: **if step 1's resource name and step 2's source name agree, the graph wires
itself.** If they don't, both halves still materialize — unconnected — and no
error is raised. Always verify:

```bash
uv run --group orchestration dagster definitions validate
```

then open `just dagster` and confirm the new asset has an edge into `staging`.

## 5. Mart column — `dbt/models/marts/fct_emissions_energy_v2.sql`

**The mart is versioned**, so the file is `_v2.sql` while the relation stays
`marts.fct_emissions_energy` (v2 is aliased back to the bare name). You do not
need to touch v1: it is a `select * exclude (…)` view over v2 and its contract is
declared `include: all`, so a new column reaches both on its own — which also
means it ships to v1's consumers without a second decision.

Add an import CTE, a `left join` on `(country_iso3, year)`, and the column in the
right source group with a `--` comment. The left joins hang off
`dim_country_year`, the spine — so coverage wider than the other sources' survives
— but **add your staging model to the `observed` CTE as well**, or the mart keeps
only the country-years the existing four report and your extra rows are filtered
out before you see them.

The spine is `stg_country` × the year range, so a country the dimension doesn't
carry can't reach the mart at all. If your source has ISO3 codes the World Bank
omits, they belong in `dbt/seeds/country_overrides.csv` (step 3's territory
problem), not in a wider join.

Update `dbt/models/marts/_marts.yml`, then `just dbt-build`. Note the partial
coverage in the column's YAML description either way — that's the convention.

## 6. Downstream

- **Polars** (`transform/co2_intensity.py`) — only if the metric is derived.
- **Evidence** (`reports/`) — new mart columns need
  `just report-clean`, not `just report`. Evidence caches each source's schema
  keyed on the source SQL, so a `select *` that gained a column looks unchanged
  and validation fails against the stale schema.
- **Docs** — the source table belongs in the `raw` list in `CLAUDE.md` and the
  "Data sources" section of `README.md`.

## Verify before you call it done

```bash
just run   # ingest -> dbt build -> transform, against the real APIs
uv run python -c "import duckdb; \
  print(duckdb.connect('data/warehouse.duckdb', read_only=True).sql(\
  'select * from marts.fct_emissions_energy limit 5').df())"
```

Check the row count didn't drop and the new column isn't all-null. Don't assume.

---

## Adding a WDI indicator

Two places, both required:

1. `WB_WDI_INDICATORS` in `ingest/pipeline.py` — the API code and its column name.
2. A `max(case when indicator_id = '<code>' then value end) as <column>` in
   `dbt/models/staging/stg_wdi.sql` — WDI arrives long (one row per
   indicator/country/year) and is pivoted wide there.

Then the mart column (step 5) and the report (step 6). The
`wdi_indicators_all_present` asset check will fail if the API returns 200 with an
empty series for the new code, which is the common failure mode.

`wb_wdi` is loaded incrementally, but the watermarks are kept **per indicator**,
so a code that isn't in the state yet is fetched in full — you get the whole
series, not the last five years. Nothing to do about it beyond re-recording the
fixtures (`just record-fixtures`), which the offline tests need anyway.
