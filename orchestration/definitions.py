"""Dagster entry point: `dagster dev` (see `[tool.dagster]` in pyproject.toml).

Everything `just run` does in sequence, expressed as one asset graph, plus the
Evidence site on the end of it.

Three jobs. The first split is about Node; the second is forced by Dagster:

* `load_retail` — the month-partitioned retail load, on its own because **an
  asset job may not span two partitions definitions**. `raw/wb_wdi` is yearly and
  `raw/retail_invoice_lines` is monthly, and `define_asset_job` resolves the
  selection to one `partitions_def` or raises ("Selected assets must have the
  same partitions definitions"). There is no opt-out: the flag that permits it
  (`allow_different_partitions_defs`) is hardcoded `False` for named asset jobs
  and set `True` only for Dagster's own implicit global job. So the second
  partitioned source has to leave, and this is the one that leaves — WDI is
  wired into `daily_refresh` and the incremental watermark, retail is a closed
  archive whose partitions only ever get replayed by hand.
* `full_refresh` — everything else bar the site. Pure Python, so `ci.yml`,
  `nightly.yml` and `release-data.yml` can run it on a bare uv checkout, and it is
  what the daily schedule targets.
* `publish_site` — `full_refresh`'s selection *plus* `reports/evidence_site`,
  which shells out to npm. `pages.yml` runs this one, after `setup-node`.

**`load_retail` has to run first**, and every caller pairs them: the justfile
recipes, all four workflows. `dbt build` reads `raw.retail_invoice_lines`, so a
`full_refresh` on a warehouse that never had the retail job run against it fails
in `stg_retail_lines` with `Catalog Error: Table with name retail_invoice_lines
does not exist!` — which is exactly how the omission was found.

It is `load_retail` and not `ingest_retail` because a job shares a namespace with
the *ops*, and `@dlt_assets(name="ingest_retail")` already took that one:
`Conflicting definitions found in repository with name 'ingest_retail'` at
definition time, naming `__ASSET_JOB` rather than the asset, which is not an
obvious read.

Both selections here name what they leave out. An asset added to `assets.py`
still joins `full_refresh` automatically; a second npm-shaped or
differently-partitioned one would have to be excluded by hand.
"""

from __future__ import annotations

import dagster as dg
from dagster import in_process_executor

from orchestration import assets
from orchestration.resources import RESOURCES

site = dg.AssetSelection.assets(assets.EVIDENCE_SITE)
retail_ingest = dg.AssetSelection.assets(*assets.raw_retail_asset.keys)

load_retail_job = dg.define_asset_job(
    name="load_retail",
    selection=retail_ingest,
    description=(
        "Load raw.retail_invoice_lines. Separate from `full_refresh` because an "
        "asset job takes a single partitions definition and this source's is "
        "monthly where wb_wdi's is yearly. Run it before `full_refresh`."
    ),
)

full_refresh_job = dg.define_asset_job(
    name="full_refresh",
    selection=dg.AssetSelection.all() - site - retail_ingest,
    description=(
        "Ingest every source bar retail, rebuild the dbt models, recompute derived "
        "metrics, rewrite the lake. Excludes the Evidence site, which needs Node, "
        "and `load_retail`, which must run first."
    ),
)

publish_site_job = dg.define_asset_job(
    name="publish_site",
    selection=dg.AssetSelection.all() - retail_ingest,
    description=(
        "`full_refresh`, then build the Evidence site from it. Requires Node >= 18 "
        "and, like `full_refresh`, a `load_retail` run before it."
    ),
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
        # Separate from the four above because each is partitioned and Dagster
        # gives a multi-asset one partitions_def for all of its assets: WDI by
        # year, retail by month.
        assets.raw_year_partitioned_assets,
        assets.raw_retail_asset,
        assets.dbt_models,
        assets.co2_intensity,
        assets.retail_rfm,
        assets.pipeline_status,
        assets.parquet_archive,
        assets.evidence_site,
    ],
    asset_checks=[
        assets.wdi_indicators_all_present,
        assets.mart_covers_recent_years,
        assets.fx_rates_reach_the_present,
        assets.co2_intensity_rank_is_dense,
        assets.rfm_scores_do_not_split_ties,
        assets.lake_matches_warehouse,
        assets.site_pages_all_rendered,
    ],
    jobs=[load_retail_job, full_refresh_job, publish_site_job],
    schedules=[daily_schedule],
    resources=RESOURCES,
    # DuckDB is a single-writer file. The default multiprocess executor would
    # happily start the Polars step next to a dlt load and lose the race for the
    # lock; one process keeps the writes serialised.
    executor=in_process_executor,
)
