"""The freshness policies the assets in `orchestration/defs/` share.

Outside `defs/` because they are not definitions: every asset, spec and check a
module in there holds at module level is registered, and a helper is safest
where nothing loads.
A schedule that quietly stopped firing shows as a stale asset, not an absent run.
"""

from __future__ import annotations

from datetime import timedelta

import dagster as dg

# Publishers push on their own cadence: two days without a load warns, a week fails.
RAW_FRESHNESS = dg.FreshnessPolicy.time_window(
    fail_window=timedelta(days=7),
    warn_window=timedelta(days=2),
)

# Rebuilt by 08:00 UTC by the daily 06:00 schedule, from data no older than midnight.
MODELLED_FRESHNESS = dg.FreshnessPolicy.cron(
    deadline_cron="0 8 * * *",
    lower_bound_delta=timedelta(hours=8),
)
