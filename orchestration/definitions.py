"""Dagster entry point: `dagster dev` (see `[tool.dagster]` in pyproject.toml).

Everything `just run` does in sequence, expressed as one asset graph. The
schedule targets `AssetSelection.all()` rather than naming steps, so an asset
added in `assets.py` joins the daily run automatically.
"""

from __future__ import annotations

import dagster as dg
from dagster import in_process_executor

from orchestration import assets
from orchestration.resources import RESOURCES

full_refresh_job = dg.define_asset_job(
    name="full_refresh",
    selection=dg.AssetSelection.all(),
    description="Ingest every source, rebuild the dbt models, recompute derived metrics.",
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
        assets.dbt_models,
        assets.co2_intensity,
        assets.pipeline_status,
        assets.parquet_archive,
    ],
    asset_checks=[
        assets.wdi_indicators_all_present,
        assets.mart_covers_recent_years,
        assets.co2_intensity_rank_is_dense,
        assets.lake_matches_warehouse,
    ],
    jobs=[full_refresh_job],
    schedules=[daily_schedule],
    resources=RESOURCES,
    # DuckDB is a single-writer file. The default multiprocess executor would
    # happily start the Polars step next to a dlt load and lose the race for the
    # lock; one process keeps the writes serialised.
    executor=in_process_executor,
)
