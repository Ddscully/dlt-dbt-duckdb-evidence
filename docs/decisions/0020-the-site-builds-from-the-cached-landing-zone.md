# 0020. The site builds from the landing zone `live.yml` caches, unless a push can change what lands in `raw`

Status: accepted 2026-10-14 (#148)

## Context

`pages.yml` ran the whole graph against the live sources on every push to
`main` that touched a build input, and every Monday. Over its 100 runs from
26 August to 14 October 2026:

- **12 failed, and 9 of those were a publisher.** The World Bank served a stale
  copy of a series six times and projections to 2050 once, and Open-Meteo timed
  out or refused twice. The other 3 were the repo's: an empty table written as
  Parquet.
- **7 of the 9 were pushes, so a commit's deploy went red for something a
  publisher did.** Five of those seven commits touched no extraction code: a
  report redesign, a check of the report's figures, a docs pass, a SQL filter
  and the move to lazy Polars plans.
- **The 28 runs after the WDI stale-copy retry (#111, 28 September) were all
  green.** By then the coupling cost attribution, not reliability: a red deploy
  on `main` could be the commit or any of six publishers. A green build took a
  median of 4 minutes, so speed was never the argument.

Each of those builds also re-fetched every WDI series in full, because no runner
carries dlt's state, and asked Open-Meteo for its lookback from a runner address
it already rations. Pull requests were never coupled: `ci.yml` runs on recorded
fixtures.

## Decision

- **`live.yml` is the refresh.** It already ran the graph against the live
  sources at 05:20 every Monday and on the 1st, 80 minutes before the Monday
  site build. It now borrows the latest release first, as the site build and
  the release do, and caches `data/lakehouse` under
  `raw-lakehouse-<epoch second>-<run id>`.
- **`pages.yml` decides per build, and says which in the run's summary:**
  - **Live**, when a push changes `LIVE_PATHS`: `ingest/`, `lake/`, the package
    modules those two import, `uv.lock` and `.python-version`. A dependency
    bump is live because a newer DuckDB may not read a catalog an older one
    wrote. A live build saves its landing zone to the cache, so a later push
    that reads what it landed finds it.
  - **Live**, when dispatched with `live`, when no cache exists, or when the
    newest is more than 15 days old: one failed weekly refresh is survived, two
    are not.
  - **Cached** otherwise: restore the newest cache, re-point its catalog at
    where it landed (`lake.lakehouse.relink`), and run `publish_site_from_raw`,
    which is `publish_site` without the `raw/` and `lake/` assets or their checks.
- Over the 88 commits on `main` since 26 August that would trigger the workflow,
  47 would have built from the cache and 41 gone live: 26 for touching
  `ingest/` or `lake/`, the other 15 for a lockfile or a package module.

## Rejected

- **Building from the release's `warehouse.duckdb`.** It is the output of the
  step a push re-runs: a push that changes a model would render its pages over
  marts the previous code built. Nor can dbt rebuild from it, because the
  staging views read `lakehouse.raw`, which is not in the file. The archive has
  to be the input, which is the landing zone.
- **A published archive of `raw`**, such as a rolling release asset or a
  workflow artifact, which any signed-in user can download from a public
  repository. `raw.retail_invoice_lines` holds 824,364 clear customer ids, which
  the export pseudonymises, and that is why `lakehouse.tar.gz` ships the weather
  archive alone. GitHub serves a cache only to workflow runs in this
  repository: a pull request's run can read it once a maintainer lets it run,
  and what it would find is the file UCI publishes.
- **A new refresh workflow.** `live.yml` already fetched everything on the
  schedule wanted, and its issue is the alert; a second job would fetch the same
  data twice a week.
- **Refreshing daily.** The sources publish annually, bar the exchange rates and
  the weather. GitHub evicts a cache unused for seven days, but a miss falls back
  to the live path, so the refresh does not have to outrun eviction.
- **Every push from the cache, with no live path.** A change to extraction code
  would first meet the live sources at the next Monday refresh, deployed in the
  meantime over a landing zone the old code fetched, and a dependency bump could
  leave the cached catalog unreadable.

## Consequences

- **A red cached build is the commit's, or the cache's**: it calls no publisher.
  A red live build can still be either, as every build could before.
- **A cached build can show data up to 15 days behind the publishers.** The
  Pipeline page's load times say when the landing zone was fetched, and
  `fx_rates_reach_the_present` warns past eight days.
- **`live.yml` no longer cold-starts the weather archive**, so the three-year
  cold start is exercised only by the fixture pipeline in `ci.yml`.
- **`LIVE_PATHS` is a list by hand.** `tests/test_workflows.py` holds it to the
  package modules `ingest/` and `lake/` import, and to the trigger list; extraction
  logic reached any other way, such as asset wiring in `orchestration/`, is not
  covered and builds from the cache.
- **Two workflows write the cache**, and both save only when the release's
  archive was carried in, so a failed download never caches a thin weather
  archive.
- **Worth revisiting** if cached builds keep missing (eviction between pushes),
  which would argue for a refresh more often than weekly; or if the 15-day limit
  starts sending pushes live because a publisher stays broken.
