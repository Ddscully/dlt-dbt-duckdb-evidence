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
| `raw` | dlt | landed source tables (`owid_co2`, `owid_energy`, `wb_country`, `wb_wdi`, `eu_elec_prices`, `ecb_fx_rates`, `retail_invoice_lines`) |
| `staging` | dbt (views) | cleaned 1:1 models (`stg_*`) at `(country_iso3, year)` grain, except `stg_eu_electricity_prices_semiannual` (Eurostat's half-years), `stg_fx_rates` (`(rate_date, quote_currency)`) and `stg_retail_lines` (`(invoice, line_number)`) |
| `marts` | dbt (tables) | `dim_country_year`, the country-year spine; `fct_emissions_energy`, the wide joined fact; `dim_grid_emission_factors`, grid factors packaged as a Scope 2 reference table; `fct_co2_estimate_versions`, revision history; `fct_eu_electricity_prices_semiannual`, EU prices at their published half-year grain; `fct_example_scope2_emissions`, the worked example over twelve invented sites; `fct_cbam_exposure`, the CBAM border cost per tonne by sourcing country; the FX and calendar tables (`dim_date`, `dim_currency`, `fct_fx_rates_*`); and the five retail models (`fct_retail_order_line`, `dim_retail_product`, `dim_retail_customer`, `fct_retail_returns`, `fct_retail_customer_cohorts`) |
| `history` | dbt (snapshots) | `snap_co2_estimates` and `snap_grid_emission_factors`, SCD2 versions of OWID's CO₂ numbers and of the Scope 2 factors. The two tables a rebuild can't reproduce |
| `analytics` | Polars | derived metrics (`co2_intensity`, `retail_rfm`) and the `pipeline_*` observability tables |

The country-year spine is the dominant grain but not a house rule.
`fct_cbam_exposure` has no year in it (a regulatory schedule, not a time series),
the FX tables have no country, and the five retail models sit below country
grain.

## The lake: `data/lake/`

`just lake` writes the year-keyed tables back out as hive-partitioned Parquet at
`data/lake/<table>/year=<year>/data_0.parquet`, zstd, 793 files and ~60 MB today.
One DuckDB file is not too small for this data. The file layout buys three things
the single file can't:

```sql
-- reads one 47 kB file, not the table: ~23 ms vs ~50 ms for the whole archive
select sum(co2) from read_parquet('data/lake/raw_owid_co2/**/*.parquet',
                                  hive_partitioning = 1)
where year = 2020;
```

- **Pruning.** The partition column is in the path, so `where year = …` never
  opens the other 274 files.
- **Portability.** Parquet outlives a DuckDB storage version, and every engine
  reads it.
- **A diffable raw layer.** The output is byte-identical run to run, so a
  restatement upstream shows up as exactly one changed file. The DuckDB file
  differs everywhere, every time.

It's an archive *of* the warehouse and not a landing zone in front of it. The
trade-off that makes is deliberate, and so is the one it doesn't get away with:
275 partitions averaging 47 kB is far too many small files for a real lake on
object storage. The two tables that come out well are the ones whose grain is
much finer than their partition column — `fct_retail_order_line` is 1.07M rows
over three year-partitions, at 13 MB, 12 MB and 836 kB. See
[CLAUDE.md](../CLAUDE.md#the-lake-lakearchivepy) for the full arithmetic.

## The lakehouse: `data/lakehouse/`

`just lakehouse` writes the same kind of Parquet through
[DuckLake](https://ducklake.select), which adds a catalog database holding
schema, snapshots and per-file statistics. It covers the two weather tables and
sits *beside* the archive rather than replacing it — the two answer the same
question, *what moved upstream?*, in different ways.

```sql
install ducklake; load ducklake;
attach 'ducklake:data/lakehouse/catalog.ducklake' as lakehouse
    (data_path 'data/lakehouse/data/');

-- what was revised in the last run, and what it used to say
select change_type, country_iso3, weather_date, temperature_2m_mean
from ducklake_table_changes('lakehouse', 'raw', 'om_weather_daily', 5, 5);
```

- **The archive answers "which file changed"; this answers "which row, and what
  did it say before".** `sha256sum` over the partitions tells you one of 793
  files moved. The change feed returns the row twice, as `update_preimage` and
  `update_postimage`.
- **A run over unchanged data writes nothing and creates no snapshot**, because
  the write is a `MERGE` gated on the row actually differing. A snapshot in the
  list is therefore evidence rather than bookkeeping.
- **No partition column.** DuckLake prunes on catalog statistics, so a filter
  still reads one file with no directory layout to arrange it —
  `raw.om_weather_daily` has no `year` column at all, and needs none.
- **The catalog is not optional.** `read_parquet` over `data/lakehouse/data/`
  will not give you the table: DuckLake writes positional delete files that the
  catalog is responsible for applying. That is the portability the hive archive
  keeps and this layer gives up, which is why both exist.

Weather is the table here because it is the only source in this warehouse that
restates on a schedule — Open-Meteo serves preliminary ERA5T and Copernicus
supersedes it with final ERA5 two to three months later, so every ingest
re-merges 90 days of daily rows in place.
