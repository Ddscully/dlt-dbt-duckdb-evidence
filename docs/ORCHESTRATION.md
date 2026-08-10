# Orchestration

`just run` chains the steps in a shell, which works right up until you want to
know *why* a table is stale, or to rebuild only what a change touched. Dagster
models the same pipeline as one asset graph:

```
raw/owid_co2      ─┐                   ┌─▶ marts/dim_country_year ─▶ marts/fct_emissions_energy ─┬─▶ analytics/co2_intensity
raw/owid_energy   ─┤                   │           (dbt)                       (dbt)                │       (Polars)
raw/wb_country    ─┼─▶ staging/stg_* ──┤                                                            └─▶ lake/parquet_archive
raw/wb_wdi        ─┤       (dbt)       └─▶ history/snap_co2_estimates ─▶ marts/fct_co2_estimate_versions  (DuckDB → Parquet)
raw/eu_elec_prices─┘                            (dbt snapshot)                       (dbt)
      (dlt)
                    ...and history/snap_grid_emission_factors ─▶ marts/dim_grid_emission_factors
                       (the same shape again) ─▶ marts/fct_example_scope2_emissions

  raw/ecb_fx_rates ─▶ staging/stg_fx_rates ─▶ the four marts/dim_date + fct_fx_rates_* tables
  raw/retail_invoice_lines ─▶ staging/stg_retail_lines ─▶ the five retail marts
                                                       └─▶ analytics/retail_rfm  (Polars)

  ...and every mart + both analytics tables + analytics/pipeline_status
                    └─▶ reports/evidence_site   (Evidence → static HTML)
```

Nothing declares that order by hand. The dlt resources are keyed `raw/<resource>`
to match the source keys dagster-dbt derives from `_sources.yml`; the
model-to-model edges come from dbt's own `ref()` graph via `manifest.json`; the
Polars asset names its upstream mart; and the Evidence site declares one dep per
table its source queries read, with a unit test that fails if a source query
starts reading a table that isn't in the list. Change a `ref()` and the graph
moves with it.

```bash
just dagster                              # UI on :3000: graph, runs, freshness, checks
just materialize                          # whole graph, headless
just materialize-site                     # ...plus the Evidence site (needs Node)
just materialize-select 'raw/wb_wdi*'     # one source + everything downstream
just backfill-wdi 1990 1995               # re-load WDI for a range of years
```

## Three jobs, and why

The site is the one asset held out of `full_refresh`. It shells out to npm, and
CI, the nightly run and the data release all want a graph that runs on a bare
Python checkout. `publish_site` is `full_refresh` plus the site, and it's what
the Pages workflow runs.

`load_retail` is separate for a different reason: an asset job can't span two
partitions definitions, and `raw/wb_wdi` is partitioned yearly while
`raw/retail_invoice_lines` is monthly. It has to run before `full_refresh`,
because dbt reads the table it lands. The justfile recipes and all four workflows
pair them.

## What that buys over the shell chain

| | |
|---|---|
| **Selective rebuilds** | `raw/wb_wdi*` reloads one API and rebuilds only what depends on it. dlt loads only that resource, so the other six keep their data. (`*` is all downstream; a bare `+` is only one layer.) |
| **Re-runnable backfills** | `raw/wb_wdi` is partitioned by year (1960 → now), so a World Bank restatement older than the five-year lookback is a unit of work you can point at instead of a 190k-row full reload. A range is one request per indicator, and `merge` on `(indicator, country_code, year)` makes re-running a year a no-op. Retail is the other partitioned source, by month. The split is on *partitioning* and not on load disposition: the ECB rates merge too, but their whole 27-year series is one three-second request, so a partition there would buy nothing. |
| **Freshness policies** | Raw assets warn after 2 days and fail after 7; modelled assets are expected by 08:00 UTC daily. A schedule that quietly stops firing turns assets stale in the UI instead of leaving no trace. |
| **Asset checks** | dbt's `not_null` tests show up as checks on the model they guard, next to Python checks dbt can't express: every WDI indicator present, mart reaching a recent year, dense ranks with no gaps, RFM scores not splitting ties. |
| **Lineage that can't drift** | The graph is derived from the dbt manifest and the dlt source, not maintained alongside them. |

A `daily_refresh` schedule (06:00 UTC) is defined but ships **stopped**. Opening
the UI shouldn't start hammering public APIs on a timer; start it yourself if you
want it running.

Dagster state lives in `.dagster/` (`DAGSTER_HOME`, exported by the justfile).
Only `dagster.yaml` is checked in. [CLAUDE.md](../CLAUDE.md#orchestration-orchestration)
covers the traps: asset-key matching between dlt and dbt, the unpartitioned
fallback inside the partitioned asset, and the fact that an asset missing from
`definitions.py` is silently absent rather than an error.
