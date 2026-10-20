"""Layer 2 — dbt staging, intermediate and marts, as one `dbt build`."""

# No `from __future__ import annotations`: Dagster refuses a string `context` annotation.

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import dagster as dg
from dagster import AssetExecutionContext
from dagster_dbt import DagsterDbtTranslator, DbtCliResource, dbt_assets, get_asset_key_for_model

from modern_data_stack.paths import dbt_target_path
from orchestration.freshness import MODELLED_FRESHNESS
from orchestration.resources import dbt_project


class FolderGroupDbtTranslator(DagsterDbtTranslator):
    """Group dbt assets by top-level folder, key versioned models under their
    schema, and give every model a freshness policy."""

    def get_asset_key(self, dbt_resource_props: Mapping[str, Any]) -> dg.AssetKey:
        # The default keys a versioned model by `[alias]` alone; prefixing the
        # schema keeps it under `marts/`, where `key:"marts/*"` selects it.
        key = super().get_asset_key(dbt_resource_props)
        if not dbt_resource_props.get("version"):
            return key
        schema = dbt_resource_props.get("config", {}).get("schema")
        return key.with_prefix(schema) if schema else key

    def get_group_name(self, dbt_resource_props: Mapping[str, Any]) -> str | None:
        # Snapshots have no subfolder, and the default names the group after each one.
        if dbt_resource_props.get("resource_type") == "snapshot":
            return dbt_resource_props.get("schema")
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
    # `build`, so the tests surface as asset checks. `pipeline_status` reads
    # `run_results.json` from this fixed `target_path`, not a per-run directory.
    yield from dbt.cli(["build"], context=context, target_path=Path(dbt_target_path())).stream()


# The marts other layers read or judge, keyed as the translator above keys them.
FCT_EMISSIONS_ENERGY = get_asset_key_for_model([dbt_models], "fct_emissions_energy")
DIM_RETAIL_CUSTOMER = get_asset_key_for_model([dbt_models], "dim_retail_customer")
FCT_FX_RATES_DAILY = get_asset_key_for_model([dbt_models], "fct_fx_rates_daily")
