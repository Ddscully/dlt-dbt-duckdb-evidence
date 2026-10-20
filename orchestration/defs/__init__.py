"""The pipeline as a Dagster asset graph, one module per layer.

    raw/*  (dlt -> DuckLake)  ->  staging/stg_*  ->  marts/fct_*  (dbt)  +->  analytics/co2_intensity  (Polars)
                                                             +->  analytics/retail_rfm     (Polars)
                                                             |      +->  analytics/pipeline_status
                                                             +->  reports/evidence_site  (Evidence)

`ingest.py` is the dlt layer, `models.py` the dbt one, `analytics.py` the Polars
assets, `site.py` the Evidence site, and `checks.py` the asset checks dbt
cannot express. `orchestration/definitions.py` loads this package with
`dg.load_assets_from_package_module` and its checks counterpart, so every asset,
asset spec and check a module in here holds at module level is registered, once
even when another module imports it, and wherever it was defined: the loaders
read each module's names, not where an object came from. One that no module in
here holds is silently absent. Nothing else loads from here: a sensor, schedule,
job or `dg.Definitions` placed in this package is ignored without an error.

The layers are wired by *asset key*, not by ordering:

* the dlt resources are keyed ``["raw", <resource>]`` to match the asset keys
  dagster-dbt derives from `dbt/models/staging/_sources.yml`;
* dagster-dbt reads `manifest.json`, so the model-to-model edges come from dbt's
  own `ref()` graph;
* each Polars asset declares the mart it reads;
* the Evidence site declares one dep per table its source queries read, from
  the maps in `publish/build_report.py`.

So changing a `ref()` or adding a source table moves the graph without a shell
script to keep in sync.
"""
