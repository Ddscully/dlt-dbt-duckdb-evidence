"""Layer 3 — the Polars derived metrics, and the pipeline's report on itself."""

# No `from __future__ import annotations`: Dagster refuses a string `context` annotation.

import dagster as dg
from dagster import AssetExecutionContext

from orchestration.defs.models import DIM_RETAIL_CUSTOMER, FCT_EMISSIONS_ENERGY
from orchestration.freshness import MODELLED_FRESHNESS
from transform.co2_intensity import run as run_co2_intensity
from transform.pipeline_status import run as run_pipeline_status
from transform.retail_rfm import run as run_retail_rfm


@dg.asset(
    key=dg.AssetKey(["analytics", "co2_intensity"]),
    deps=[FCT_EMISSIONS_ENERGY],
    group_name="analytics",
    kinds={"polars", "duckdb"},
    freshness_policy=MODELLED_FRESHNESS,
    description=(
        "CO2 per $ GDP (derived from World Bank GDP, not OWID's shorter "
        "co2_per_gdp), dense-ranked within each (income group, year) cohort."
    ),
)
def co2_intensity(context: AssetExecutionContext) -> dg.MaterializeResult:
    rows = run_co2_intensity()
    context.log.info("wrote analytics.co2_intensity (%s rows)", rows)
    return dg.MaterializeResult(metadata={"dagster/row_count": rows})


@dg.asset(
    key=dg.AssetKey(["analytics", "retail_rfm"]),
    deps=[DIM_RETAIL_CUSTOMER],
    group_name="analytics",
    kinds={"polars", "duckdb"},
    freshness_policy=MODELLED_FRESHNESS,
    description=(
        "RFM scores and segments per customer. Quintiles are cut on value "
        "rather than on rank position — SQL's `ntile` would split the 1,626 "
        "one-order customers across two buckets."
    ),
)
def retail_rfm(context: AssetExecutionContext) -> dg.MaterializeResult:
    rows = run_retail_rfm()
    context.log.info("wrote analytics.retail_rfm (%s rows)", rows)
    return dg.MaterializeResult(metadata={"dagster/row_count": rows})


@dg.asset(
    key=dg.AssetKey(["analytics", "pipeline_status"]),
    # Inventories `analytics`, so it follows every table written there.
    deps=[co2_intensity, retail_rfm],
    group_name="analytics",
    kinds={"polars", "duckdb"},
    freshness_policy=MODELLED_FRESHNESS,
    description=(
        "Pipeline observability: dlt load times per source, row counts and year "
        "spans per layer, and the stored-failure count for every dbt test. "
        "Rendered by the Evidence 'Pipeline' page."
    ),
)
def pipeline_status(context: AssetExecutionContext) -> dg.MaterializeResult:
    written = run_pipeline_status()
    for name, rows in written.items():
        context.log.info("wrote analytics.%s (%s rows)", name, rows)
    return dg.MaterializeResult(
        metadata={"dagster/row_count": sum(written.values()), "tables": written}
    )
