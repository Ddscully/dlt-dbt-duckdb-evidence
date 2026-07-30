"""Dagster entry point: `dagster dev` (see `[tool.dagster]` in pyproject.toml).

Everything `just run` does in sequence, expressed as one asset graph, plus the
Evidence site on the end of it.

Two jobs, and the split is about Node, not about layering:

* `full_refresh` — the warehouse and the lake. Pure Python, so `ci.yml`,
  `nightly.yml` and `release-data.yml` can run it on a bare uv checkout, and it is
  what the daily schedule targets.
* `publish_site` — the same graph *plus* `reports/evidence_site`, which shells out
  to npm. `pages.yml` runs this one, after `setup-node`.

`full_refresh` is therefore the only selection here that names what it excludes.
An asset added to `assets.py` still joins it automatically; a second npm-shaped
one would have to be excluded by hand.
"""

from __future__ import annotations

import dagster as dg
from dagster import in_process_executor

from orchestration import assets
from orchestration.resources import RESOURCES

site = dg.AssetSelection.assets(assets.EVIDENCE_SITE)

full_refresh_job = dg.define_asset_job(
    name="full_refresh",
    selection=dg.AssetSelection.all() - site,
    description=(
        "Ingest every source, rebuild the dbt models, recompute derived metrics, "
        "rewrite the lake. Everything except the Evidence site, which needs Node."
    ),
)

publish_site_job = dg.define_asset_job(
    name="publish_site",
    selection=dg.AssetSelection.all(),
    description="`full_refresh`, then build the Evidence site from it. Requires Node >= 18.",
)

daily_schedule = dg.ScheduleDefinition(
    name="daily_refresh",
    job=full_refresh_job,
    cron_schedule="0 6 * * *",
    execution_timezone="UTC",
    # Off by default: this is a demo repo, and `dagster dev` shouldn't start
    # hitting public APIs on a timer just because someone opened the UI.
    default_status=dg.DefaultScheduleStatus.STOPPED,
)

defs = dg.Definitions(
    # Listed explicitly: an asset defined in `assets.py` but missing here is not
    # in the graph at all, and `AssetSelection.all()` won't tell you.
    assets=[
        assets.raw_assets,
        # Separate from the four above because it is partitioned by year and
        # Dagster gives a multi-asset one partitions_def for all of its assets.
        assets.raw_wdi_asset,
        assets.dbt_models,
        assets.co2_intensity,
        assets.pipeline_status,
        assets.parquet_archive,
        assets.evidence_site,
    ],
    asset_checks=[
        assets.wdi_indicators_all_present,
        assets.mart_covers_recent_years,
        assets.co2_intensity_rank_is_dense,
        assets.lake_matches_warehouse,
        assets.site_pages_all_rendered,
    ],
    jobs=[full_refresh_job, publish_site_job],
    schedules=[daily_schedule],
    resources=RESOURCES,
    # DuckDB is a single-writer file. The default multiprocess executor would
    # happily start the Polars step next to a dlt load and lose the race for the
    # lock; one process keeps the writes serialised.
    executor=in_process_executor,
)
