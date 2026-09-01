# The warehouse

What the pipeline loads, how it's laid out, and the Parquet archive beside it.
The [README](../README.md) has the short version.

## Data sources

Seven feeds from five publishers, all freely licensed and small enough to run
locally. Six are country-keyed; the seventh isn't a country dataset at all. The
CBAM default values are an eighth source that arrives as a seed instead of a
feed, for the reasons in
[the `compliance-models` skill](../.claude/skills/compliance-models/SKILL.md).

| Dataset | Grain | Link |
|---------|-------|------|
| OWID CO₂ & GHG | country-year (fact) | https://github.com/owid/co2-data |
| OWID Energy | country-year (fact) | https://github.com/owid/energy-data |
| World Bank WDI: GDP, life expectancy, population, poverty | country-year (fact) | https://databank.worldbank.org/source/world-development-indicators |
| World Bank countries: region & income group | country (dimension) | https://api.worldbank.org/v2/country?format=json |
| Eurostat: household electricity prices (EU/EEA) | country-half-year (fact) | https://ec.europa.eu/eurostat/databrowser/view/nrg_pc_204 |
| ECB euro reference rates, via Frankfurter | date-currency (fact), the first sub-annual grain | https://frankfurter.dev |
| UCI Online Retail II: one retailer's invoice lines | invoice-line (fact), the finest grain here and the only one below a country | https://archive.ics.uci.edu/dataset/502/online+retail+ii |

Joins are on **ISO country code + year**, yielding marts like *"CO₂ per \$ of GDP
by income group over time"* and *"renewables adoption vs. life expectancy."*

The sources don't agree on coverage. The fact is therefore built on an explicit
country-year spine (`dim_country_year`) instead of off whichever source happens
to be widest, so a country-year that only Eurostat or only the World Bank reports
still lands, carrying nulls in the columns the others don't fill.

### Three sources keep a finer grain of their own

Eurostat publishes half-yearly. `fct_eu_electricity_prices_semiannual` holds the
published halves alongside the annual average that joins to everything else.
Averaging is what the annual grain costs, and it costs a lot: half-over-half
price moves averaged 19% across countries in 2022 against 3–4% through the 2010s.
Both grains are in the warehouse for that reason.

The ECB's series is daily and has no country in it at all, which is what forced
the warehouse's first calendar (`dim_date`), its first gap-filling decision and
its first `materialized='incremental'` model. See the
[Currency page](https://ddscully.github.io/dlt-dbt-duckdb-evidence/currency).

The retailer's grain is a single invoice line at a timestamp, below a country
rather than beside one, and it's the only source here that isn't a statistical
publication. Nothing about it has been cleaned by anyone, so the modelling is the
value: what counts as a return, which rows are revenue, and who the customer is
when 22.8% of lines have no id. See the
[Retail page](https://ddscully.github.io/dlt-dbt-duckdb-evidence/retail).

### Two write dispositions, two load calls

Four of the seven resources load with dlt's `replace` disposition: both OWID
files, the World Bank country list and Eurostat prices. They're small enough that
a full reload every run is the honest default, and it keeps dlt re-inferring the
schema so an upstream type change fails loudly.

The other three are incremental. WDI is the biggest pull (~190k rows across 11
indicators) and loads with `merge` on `(indicator, country_iso3, year)` over a
five-year window. That window is a lookback and not "everything newer than last
time", because the World Bank restates years it has already published.

The two dispositions need two `run()` calls. `refresh` is a property of a run, so
refreshing the replace tables in the same call would drop the incremental ones'
history along with their watermarks. `load_groups()` in `ingest/pipeline.py` is
what both `main()` and the Dagster assets iterate.

A restatement older than the window doesn't need the whole series pulled again.
WDI is partitioned by year in the asset graph, so `just backfill-wdi 1997`
re-fetches that year and merges it in.

## Schemas

One DuckDB file, `data/warehouse.duckdb`:

| Schema | Written by | Contents |
|--------|-----------|----------|
| `raw` | dlt | landed source tables (`owid_co2`, `owid_energy`, `wb_country`, `wb_wdi`, `eu_elec_prices`, `ecb_fx_rates`, `retail_invoice_lines`, `om_weather_daily`) |
| `staging` | dbt (views) | cleaned 1:1 models (`stg_*`) at `(country_iso3, year)` grain, except `stg_eu_electricity_prices_semiannual` (Eurostat's half-years), `stg_fx_rates` (`(rate_date, quote_currency)`) and `stg_retail_lines` (`(invoice, line_number)`) |
| `intermediate` | dbt (views) | `int_*`, the derivations two models share or one model should be tested apart from: `int_country_year_observed` (the country-years the four country-stats sources report, read by both the spine and the wide fact), `int_cbam_default_factors` (Annex I's row-level fallback rule) and `int_retail_return_matches` (the returns-to-purchase inference). `private`, uncontracted, and not published as Parquet |
| `marts` | dbt (tables) | `dim_country_year`, the country-year spine; `fct_emissions_energy`, the wide joined fact; `dim_grid_emission_factors`, grid factors packaged as a Scope 2 reference table; `fct_co2_estimate_versions`, revision history; `fct_eu_electricity_prices_semiannual`, EU prices at their published half-year grain; `fct_example_scope2_emissions`, the worked example over twelve invented sites; `fct_cbam_exposure`, the CBAM border cost per tonne by sourcing country; the FX and calendar tables (`dim_date`, `dim_currency`, `fct_fx_rates_*`); and the five retail models (`fct_retail_order_line`, `dim_retail_product`, `dim_retail_customer`, `fct_retail_returns`, `fct_retail_customer_cohorts`) |
| `history` | dbt (snapshots) | `snap_co2_estimates` and `snap_grid_emission_factors`, SCD2 versions of OWID's CO₂ numbers and of the Scope 2 factors. The two tables a rebuild can't reproduce |
| `analytics` | Polars | derived metrics (`co2_intensity`, `retail_rfm`) and the `pipeline_*` observability tables |

The country-year spine is the dominant grain but not a house rule.
`fct_cbam_exposure` has no year in it (a regulatory schedule, not a time series),
the FX tables have no country, and the five retail models sit below country
grain.

Below country grain is not outside it. `fct_retail_order_line`,
`fct_retail_returns`, `dim_retail_customer` and `analytics.retail_rfm` all carry
`country_iso3` beside the source's own country label, resolved once in
`stg_retail_lines` through the `retail_country_map` seed — so retail revenue can
be grouped by `region` or `income_group`, or put beside the electricity price
its market pays. The source spells nine of its 43 country labels in ways the
dimension does not (`EIRE`, `RSA`, `USA`, `Korea`, `Czech Republic`,
`Hong Kong`, and three that are not countries at all), which is why the
resolution is a seed rather than a join on name.

## The lakehouse: `data/lakehouse/`

**This is where `raw` lives.** dlt lands the eight source tables into a
[DuckLake](https://ducklake.select) catalog — plain Parquet under
`data/lakehouse/data/`, plus a catalog database holding schema, snapshot lineage
and per-file statistics. dbt attaches it and builds `staging`,
`intermediate`, `marts` and `history` into `data/warehouse.duckdb`, which is the only thing the release
publishes. `just lakehouse` reports what the catalog holds; `just ingest` fills
it.

```sql
install ducklake; load ducklake;
attach 'ducklake:duckdb:data/lakehouse/catalog.duckdb' as lakehouse
    (data_path 'data/lakehouse/data/');

select count(*) from lakehouse.raw.om_weather_daily;
```

There used to be a hive-partitioned archive beside this at `data/lake/`, written
by hand from the warehouse. It is gone: it was a second copy of data DuckLake
already stores as Parquet, and keeping both meant maintaining two answers to
*what moved upstream?*.

- **Ask what changed by diffing two snapshots, not with the change feed.**
  `ducklake_table_changes()` is the obvious tool and it does not survive dlt,
  which regenerates `_dlt_id` and `_dlt_load_id` on every row it re-merges —
  reloading 500 identical rows reports 500 `update_preimage`/`update_postimage`
  pairs. The feed is right; the writer makes it meaningless. So:

```python
from lake.lakehouse import WEATHER_TABLE, revisions, versions

v = versions(WEATHER_TABLE)              # snapshots in which this table changed
revisions(WEATHER_TABLE, v[-2], v[-1])   # rows that genuinely differ
```

  which projects the provenance columns away and returns **0 rows** for a no-op
  reload and exactly the changed rows for a real restatement. It also works
  between *any* two snapshots, so "what changed since last month" is one query.

- **No partition column, and none needed.** DuckLake prunes on catalog
  statistics, so a filter still reads one file with no directory layout to
  arrange it. `raw.om_weather_daily` is keyed on `weather_date` and has no `year`
  column — the hive archive would have needed one invented for the layout.
- **The catalog is not optional.** `read_parquet` over `data/lakehouse/data/`
  will not give you the table: DuckLake writes positional delete files that only
  the catalog applies, so a glob either fails on a schema mismatch or returns
  superseded rows alongside current ones.
- **Deleting `data/lakehouse/` is the destructive act in this repo now.** It is
  the only copy of every landing table, and it holds both the snapshot lineage
  (which no rebuild invents) and the capital-city weather archive (which no
  rebuild can afford — days of Open-Meteo's daily budget). `just clean` does not
  list it.

Weather is the table worth diffing because it is the only source here that
restates on a schedule — Open-Meteo serves preliminary ERA5T and Copernicus
supersedes it with final ERA5 two to three months later, so every ingest
re-merges 90 days of daily rows in place.
