"""Dagster entry point: `just dagster` (see `[tool.dg]` in pyproject.toml).

Everything `just run` does, as one asset graph, plus the Evidence site. The
assets and checks are `orchestration/defs/`, loaded whole; what is written out
here is what has no layer of its own. Four jobs:

* `load_retail` — the month-partitioned retail load. A job takes its
  partitions definition from its assets, so with retail inside it `full_refresh`
  would be month-partitioned, and a partitioned job's Materialize button in the
  UI launches a backfill instead of the routine load.
* `full_refresh` — everything else bar the site, and unpartitioned: WDI and
  weather take their backfill years as run config (`YearRange`), not partitions.
  Pure Python, so `ci.yml`, `live.yml` and `release-data.yml` run it without
  Node; the daily schedule targets it.
* `publish_site` — `full_refresh` plus `reports/evidence_site`, which shells out
  to npm. `pages.yml` runs it when a push can change what lands in `raw`.
* `publish_site_from_raw` — `publish_site` without anything that writes the
  landing zone: every `raw/` asset and `lake/snapshot_expiry`. It builds from
  whatever `raw` already holds, which in `pages.yml` is the landing zone
  `live.yml` cached, so a push that cannot change `raw` does not call a
  publisher at all.

`load_retail` must run before the two jobs that ingest, because `dbt build`
reads `raw.retail_invoice_lines`; the justfile recipes and all four workflows
pair the jobs. It is not called `ingest_retail` because jobs share a namespace
with ops, and the retail `@dlt_assets` op has that name.

Every selection names what it excludes, so a new asset joins `full_refresh`
automatically and a new `raw/` asset stays out of `publish_site_from_raw`; a
second npm-shaped or partitioned asset has to be excluded by hand, and
`tests/test_definitions.py` fails on a partitioned one.
"""

from __future__ import annotations

from pathlib import Path

import dagster as dg
from dagster import in_process_executor

from orchestration.defs.ingest import raw_retail_asset
from orchestration.defs.site import EVIDENCE_SITE
from orchestration.resources import RESOURCES

site = dg.AssetSelection.assets(EVIDENCE_SITE)
retail_ingest = dg.AssetSelection.assets(*raw_retail_asset.keys)
# By prefix, not by block: a new `@dlt_assets` block lands under `raw/` and is
# excluded without anyone remembering to. The checks on these assets go with
# them, because they judge what an ingest landed, and a job that lands nothing
# has nothing for them to judge. A cache is saved only after a green
# materialize that ran them, so they have already judged what it holds.
landing_zone = dg.AssetSelection.key_prefixes("raw", "lake")

load_retail_job = dg.define_asset_job(
    name="load_retail",
    selection=retail_ingest,
    description=(
        "Load raw.retail_invoice_lines. Separate from `full_refresh` because this "
        "source is partitioned by month, and a partitioned job's Materialize "
        "button launches a backfill. Run it before `full_refresh`."
    ),
)

full_refresh_job = dg.define_asset_job(
    name="full_refresh",
    selection=dg.AssetSelection.all() - site - retail_ingest,
    description=(
        "Ingest every source bar retail, rebuild the dbt models, recompute derived "
        "metrics. Excludes the Evidence site, which needs Node, and "
        "`load_retail`, which must run first."
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

publish_site_from_raw_job = dg.define_asset_job(
    name="publish_site_from_raw",
    selection=dg.AssetSelection.all() - landing_zone,
    description=(
        "`publish_site` without the ingest: rebuild the dbt models, the derived "
        "metrics and the Evidence site from what the landing zone already holds. "
        "Calls no publisher, so it needs no `load_retail` run first."
    ),
)

daily_schedule = dg.ScheduleDefinition(
    name="daily_refresh",
    job=full_refresh_job,
    cron_schedule="0 6 * * *",
    execution_timezone="UTC",
    # Off by default: this is a demo repo, and `just dagster` shouldn't start
    # hitting public APIs on a timer just because someone opened the UI.
    default_status=dg.DefaultScheduleStatus.STOPPED,
)

defs = dg.Definitions.merge(
    # Every asset and check defined at module level under `orchestration/defs/`,
    # found by import: there is no list here for a new one to be missing from.
    dg.load_from_defs_folder(path_within_project=Path(__file__).parent),
    dg.Definitions(
        jobs=[load_retail_job, full_refresh_job, publish_site_job, publish_site_from_raw_job],
        schedules=[daily_schedule],
        resources=RESOURCES,
        # DuckDB takes one writer. The default multiprocess executor would run steps
        # side by side and lose the race for the lock; one process serialises them.
        executor=in_process_executor,
    ),
)
