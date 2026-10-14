# 0021. A site build is diffed from the commit that built its cache

Status: accepted 2026-10-14 (#153)

## Context

[0020](0020-the-site-builds-from-the-cached-landing-zone.md) has `pages.yml`
build from the cached landing zone unless something can change what lands in
`raw`. It answered "since when" with the last successful `pages.yml` run on
`main`, looked up with `gh run list`, and asked the question on a push alone.
That base is not the code that fetched the cache, and the gap shows in three
places:

- **A cached build becomes the base and saves nothing.** If a push that
  changes `ingest/` goes live and fails on a publisher, the Monday rebuild,
  which is not diffed, builds that commit green from the cache. The next push
  is diffed from the Monday run, sees no `ingest/` change, and takes the cache
  too: the new code deploys over a landing zone the old code fetched.
- **So does a live build whose borrow soft-failed.** It succeeds without
  carrying the release's weather archive in, so it saves no cache, but it still
  becomes the base, and the push after it is diffed past an `ingest/` change
  the cache never saw.
- **The Monday rebuild and a plain dispatch were never diffed.** They took
  whatever cache was newest, including one from a `live.yml` run that started
  before an extraction change merged and finished after it. 0020 accepted that
  window as a Monday morning's length.

The first gap has already been crossed once, harmlessly: #151's build was
diffed from #150's, which had built from the cache and saved nothing. #151's
own `ingest/` change came after that base, so it still went live.

The cache already knew where it came from: every entry is saved by a run on a
known commit. Only the key did not say which.

## Decision

- **The key carries the commit**:
  `raw-lakehouse-<epoch second>-<run id>-<sha>`, stamped the same way by
  `live.yml` and by a live `pages.yml` build.
- **Every build looks the cache up and is diffed from that commit**, whatever
  started it; only a dispatch with `live` skips the lookup. One step decides,
  in order:
  - no cache → live;
  - a key whose epoch or 40-character SHA cannot be read → live;
  - older than `RAW_CACHE_MAX_AGE_DAYS` → live;
  - the commit cannot be fetched → live;
  - `git diff --no-renames` from that commit to the build's touches
    `LIVE_PATHS` → live;
  - otherwise cached.
- **The run's summary names the cache's short SHA and age**, and which files
  sent it live.
- `gh run list`, its token and the workflow's `actions: read` permission go.

## Rejected

- **The last successful run, as 0020 had it.** Above: success is not the same
  event as saving a cache, so the base can move past code nothing fetched with.
- **The last run that saved a cache**, found through the Actions API. It closes
  the first two gaps but not the third, since `live.yml` saves caches too, and
  it keeps a lookup that fails open when the API or the permission does. The key
  is already in hand once the cache is.
- **The push's own `before`.** GitHub cancels a run still waiting when a newer
  one queues, and a failed live build leaves its change unbuilt; diffed one
  push at a time, either change reaches the site over the old landing zone.
- **Diffing pushes only, and trusting the schedule.** The Monday rebuild runs
  80 minutes after `live.yml`, which is usually, not always, enough for that
  run to have fetched with today's code.

## Consequences

- **The first build after this merges goes live once**, because every cache
  saved before it carries the old key, which cannot be read. It saves the first
  key in the new form.
- **A cached build now depends on fetching one more commit** from `origin`. A
  failed fetch goes live, green, so an outage there costs the decoupling for
  that build, not the deploy.
- **A cache saved on a branch dry run names a branch commit**; GitHub serves
  it only to runs on that branch, where diffing from it is right.
- **The `live.yml` race 0020 accepted is closed**: a cache fetched by older
  code is diffed like any other and goes live if the code since touches
  `LIVE_PATHS`.
- **Worth revisiting** if GitHub's cache keys change shape or length limits, or
  if a build needs to know more about the cache than its commit and age, at
  which point a manifest saved inside the cache beats a longer key.
