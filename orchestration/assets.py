"""The pipeline as a Dagster asset graph.

    raw/*  (dlt)  ->  staging/stg_*  ->  marts/fct_*  (dbt)  ->  analytics/co2_intensity  (Polars)

The three layers are wired by *asset key*, not by ordering:

* the dlt resources are keyed ``["raw", <resource>]`` to match the asset keys
  dagster-dbt derives from `dbt/models/staging/_sources.yml`;
* dagster-dbt reads `manifest.json`, so the model-to-model edges come from dbt's
  own `ref()` graph;
* the Polars asset declares `deps=[marts/fct_emissions_energy]`.

The upshot: changing a `ref()` or adding a source table moves the graph, and
there is no shell script to keep in sync.
"""

# NB: no `from __future__ import annotations` here — Dagster inspects the
# `context` parameter's annotation object, and a stringified one fails its check.

from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

import dagster as dg
import duckdb
from dagster import AssetExecutionContext
from dagster_dbt import DagsterDbtTranslator, DbtCliResource, dbt_assets, get_asset_key_for_model
from dagster_dlt import DagsterDltResource, DagsterDltTranslator, dlt_assets
from dagster_dlt.translator import DltResourceTranslatorData

from ingest.pipeline import REFRESH, WB_WDI_INDICATORS, build_pipeline, public_indicators
from orchestration.resources import dbt_project
from transform.co2_intensity import DUCKDB_PATH, run as run_co2_intensity

# --------------------------------------------------------------------------- #
# Freshness policies
# --------------------------------------------------------------------------- #
# These state what *should* be true regardless of whether a run happened, so a
# schedule that quietly stopped firing shows up as a stale asset in the UI
# instead of having to be inferred from an absent run.

# Raw pulls: upstream publishers push on their own cadence, so two days without
# a successful load is worth a warning and a week is a failure.
RAW_FRESHNESS = dg.FreshnessPolicy.time_window(
    fail_window=timedelta(days=7),
    warn_window=timedelta(days=2),
)

# Modelled layers hang off the daily 06:00 schedule: rebuilt by 08:00 UTC from
# data no older than the preceding midnight.
MODELLED_FRESHNESS = dg.FreshnessPolicy.cron(
    deadline_cron="0 8 * * *",
    lower_bound_delta=timedelta(hours=8),
)


# --------------------------------------------------------------------------- #
# Layer 1 — dlt ingestion
# --------------------------------------------------------------------------- #

RAW_DESCRIPTIONS = {
    "owid_co2": "Our World in Data CO2 & GHG emissions, country-year (CSV).",
    "owid_energy": "Our World in Data energy production/consumption, country-year (CSV).",
    "wb_country": "World Bank country dimension: region, income group, capital (JSON API).",
    "wb_wdi": "World Bank WDI indicators, long-format (JSON API, paginated).",
    "eu_elec_prices": "Eurostat nrg_pc_204 household electricity prices, EU/EEA (JSON-stat).",
}


class RawSchemaDltTranslator(DagsterDltTranslator):
    """Key dlt resources as ``raw/<resource>``.

    dagster-dlt would otherwise name them ``dlt_public_indicators_<resource>``,
    which wouldn't line up with the ``raw`` source keys dagster-dbt generates —
    and the two halves of the graph would sit side by side, unconnected.
    """

    def get_asset_spec(self, data: DltResourceTranslatorData) -> dg.AssetSpec:
        name = data.resource.name
        return (
            super()
            .get_asset_spec(data)
            .replace_attributes(
                key=dg.AssetKey(["raw", name]),
                group_name="ingestion",
                description=RAW_DESCRIPTIONS.get(name),
                freshness_policy=RAW_FRESHNESS,
                # dlt resources are independent HTTP pulls; the default
                # translator would make them depend on a synthetic source asset.
                deps=[],
            )
        )


@dlt_assets(
    dlt_source=public_indicators(),
    dlt_pipeline=build_pipeline(),
    dagster_dlt_translator=RawSchemaDltTranslator(),
    name="ingest_public_indicators",
)
def raw_assets(context: AssetExecutionContext, dlt: DagsterDltResource):
    # One op for all five resources: DuckDB takes a single writer, so fanning
    # these out into parallel steps would just make them fight over the file.
    # dlt still loads only the resources whose assets were selected.
    yield from dlt.run(context=context, refresh=REFRESH)


# --------------------------------------------------------------------------- #
# Layer 2 — dbt staging + marts
# --------------------------------------------------------------------------- #


class FolderGroupDbtTranslator(DagsterDbtTranslator):
    """Group dbt assets by their folder (`staging`, `marts`) and give them a freshness policy."""

    def get_group_name(self, dbt_resource_props: Mapping[str, Any]) -> str | None:
        fqn = dbt_resource_props.get("fqn") or []
        # fqn is [project, <subfolders...>, name]
        return fqn[1] if len(fqn) > 2 else super().get_group_name(dbt_resource_props)

    def get_asset_spec(self, manifest, unique_id, project) -> dg.AssetSpec:
        spec = super().get_asset_spec(manifest, unique_id, project)
        return spec.replace_attributes(freshness_policy=MODELLED_FRESHNESS)


@dbt_assets(
    manifest=dbt_project.manifest_path,
    dagster_dbt_translator=FolderGroupDbtTranslator(),
)
def dbt_models(context: AssetExecutionContext, dbt: DbtCliResource):
    # `build` = run + test, so dbt's schema tests surface as Dagster asset checks
    # on the model they belong to.
    yield from dbt.cli(["build"], context=context).stream()


FCT_EMISSIONS_ENERGY = get_asset_key_for_model([dbt_models], "fct_emissions_energy")


# --------------------------------------------------------------------------- #
# Layer 3 — Polars derived metrics
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# Asset checks — the ones dbt can't express
# --------------------------------------------------------------------------- #


def _scalar(query: str):
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    try:
        return con.sql(query).fetchone()[0]
    finally:
        con.close()


@dg.asset_check(asset=dg.AssetKey(["raw", "wb_wdi"]), blocking=True)
def wdi_indicators_all_present() -> dg.AssetCheckResult:
    """Every configured indicator landed at least one row.

    `_get_json` now raises on a non-2xx, but the World Bank also answers a bad
    indicator code with a 200 and an empty series — which would quietly become
    an all-null column in `stg_wdi` rather than a failure.
    """
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    try:
        found = {r[0] for r in con.sql("select distinct indicator from raw.wb_wdi").fetchall()}
    finally:
        con.close()
    missing = sorted(set(WB_WDI_INDICATORS) - found)
    return dg.AssetCheckResult(
        passed=not missing,
        metadata={"missing_indicators": missing, "indicators_loaded": len(found)},
    )


@dg.asset_check(asset=FCT_EMISSIONS_ENERGY)
def mart_covers_recent_years() -> dg.AssetCheckResult:
    """The mart should reach within two years of today.

    A source that silently stops updating shows up here long before anyone
    notices a dashboard has gone flat.
    """
    max_year = _scalar("select max(year) from marts.fct_emissions_energy")
    current_year = date.today().year
    lag = current_year - max_year
    return dg.AssetCheckResult(
        passed=lag <= 2,
        severity=dg.AssetCheckSeverity.WARN,
        metadata={"max_year": max_year, "years_behind": lag},
    )


@dg.asset_check(asset=co2_intensity, blocking=True)
def co2_intensity_rank_is_dense() -> dg.AssetCheckResult:
    """Each (income_group, year) cohort ranks from 1 with no gaps."""
    bad = _scalar(
        """
        select count(*) from (
            select income_group, year
            from analytics.co2_intensity
            group by income_group, year
            having min(co2_intensity_rank) <> 1
                or max(co2_intensity_rank) <> count(distinct co2_intensity_rank)
        )
        """
    )
    return dg.AssetCheckResult(passed=bad == 0, metadata={"bad_cohorts": bad})


__all__ = ["FCT_EMISSIONS_ENERGY", "co2_intensity", "dbt_models", "raw_assets"]
