"""Layer 4 — the Evidence site, built from the warehouse it reports on."""

# No `from __future__ import annotations`: Dagster refuses a string `context` annotation.

import dagster as dg
from dagster import AssetExecutionContext
from dagster_dbt import get_asset_key_for_model

from orchestration.defs import models
from orchestration.freshness import MODELLED_FRESHNESS
from publish.build_report import TABLE_TO_ASSET_KEY, TABLE_TO_DBT_MODEL, run as build_report

EVIDENCE_SITE = dg.AssetKey(["reports", "evidence_site"])

# One dep per table the source queries read, so the graph shows what a stale page
# depends on. `publish.build_report` owns the mapping.
SITE_DEPS = [
    *(
        get_asset_key_for_model([models.dbt_models], model)
        for model in sorted(set(TABLE_TO_DBT_MODEL.values()))
    ),
    # The `pipeline_*` tables share one asset, and Dagster rejects a duplicated dep.
    *(dg.AssetKey(list(key)) for key in dict.fromkeys(TABLE_TO_ASSET_KEY.values())),
]


@dg.asset(
    key=EVIDENCE_SITE,
    deps=SITE_DEPS,
    group_name="reports",
    kinds={"evidence", "duckdb"},
    freshness_policy=MODELLED_FRESHNESS,
    description=(
        "The Evidence dashboard as a static site in `reports/build/`: extracts "
        "the warehouse tables to parquet (`npm run sources:strict`), then renders "
        "every page under `reports/pages/` against them, and puts the dbt docs "
        "beside them at `dbt/`. Published by "
        "`.github/workflows/pages.yml`."
    ),
)
def evidence_site(context: AssetExecutionContext) -> dg.MaterializeResult:
    # Needs Node, so `full_refresh` leaves it out.
    summary = build_report()
    context.log.info(
        "built %s pages from %s source queries (%s files, %.1f MB)",
        summary["pages"],
        summary["source_queries"],
        summary["files"],
        summary["bytes"] / 1e6,
    )
    if summary["copied_to_site_root"]:
        context.log.info("copied the site to %s", summary["site_root"])
    return dg.MaterializeResult(
        metadata={
            "pages": summary["pages"],
            "files": summary["files"],
            "bytes": summary["bytes"],
            "dbt_docs_bytes": summary["dbt_docs_bytes"],
            "warehouse_tables": summary["warehouse_tables"],
            "build_dir": dg.MetadataValue.path(summary["build_dir"]),
            "site_root": dg.MetadataValue.path(summary["site_root"]),
        }
    )
