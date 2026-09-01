---
name: weather-models
description: The Open-Meteo ERA5 capital-city weather source — raw.om_weather_daily, stg_weather_daily, the weighted rate-limit budget that bounds what can be fetched, the positional multi-location response, the two degree-day conventions and the yearly partition it shares with wb_wdi. Use when editing the weather resource or model, running just backfill-weather, changing which years or locations the archive covers, or reasoning about anything the weather budget constrains.
---

# Capital-city weather (`om_weather_daily`, `stg_weather_daily`)


Daily ERA5 weather for the 41 EU/EEA capitals, from Open-Meteo. Added because
`stg_country` had carried the World Bank's capital `latitude`/`longitude` since
the first commit and **nothing read either column** — this file mentioned them
once, as a `try_cast` ingest gotcha. It is the warehouse's first spatial join and
its first source with a *finite budget*.

- **The binding constraint is a rate limit, not disk.** The obvious cost model
  prices rows, and on that basis the whole 1940- global archive is trivial: 211
  capitals x 86 years x 6 variables is ~110 MB in DuckDB, nowhere near the 2 GiB
  release-asset cap. The actual ceiling is Open-Meteo's published weighted
  budget — **600 units a minute, 5,000 an hour, 10,000 a day** — where one
  request costs `(variables / 10) * (days / 14) * locations`. That same global
  archive is ~286,000 units: **28 days of allowance**. Scope here was chosen
  against the budget and the storage question never came into it.
- **The charge lands *after* the response, which is why the limit looks
  inconsistent.** A single 86-year three-variable request costs ~673 units
  against a 600-a-minute budget and is *served*; the next one is refused. So an
  oversized request is not an error to prevent, and `WeightedWindowLimiter`
  drains the window and lets it overshoot into debt rather than refusing it or
  spinning forever waiting for it to "fit".
- **Batching locations is the lever, and it is counter-intuitive.** Weight is
  charged partly per *request*, so five locations in one call cost far less than
  five calls — measured at the point where a one-location 86-year request was
  being refused while a five-location one of the same span was served. This is
  the opposite shape to `wb_wdi`, whose eight-thread pool is right precisely
  because its only cost is latency. A thread pool here would be the one thing a
  shared budget cannot absorb.
- **The bulk archive is real, open, and the wrong trade.** `s3://openmeteo`
  (us-west-2, anonymous, CC BY 4.0) publishes ERA5 back to 1940 — but
  `temperature_2m` alone is **368 GB** across 164 files averaging 2.2 GB, global
  gridded, in Open-Meteo's own `.om` format needing their Docker/Swift
  toolchain. Their own tutorial syncs two years of ERA5-Land at ~8 GB, more than
  this entire repo. Checked so nobody checks again.
- **The data licence and the API terms are separate, and only one of them
  travels.** The numbers are CC BY 4.0, so the release redistributes them like
  every other source. The *free tier* is additionally non-commercial and capped
  at 10,000 calls a day, which binds this pipeline and follows nobody who
  downloads the result. Attribution names Copernicus/ECMWF as well as
  Open-Meteo, because ERA5 is theirs.
- **The response is matched to the request by *position*, and there is no other
  key.** A multi-location response is a JSON array whose entries carry a
  `location_id` — except the first, which has none at all (absent, 1, 2, ...).
  So `weather_locations()` sorts, and **fails closed** when a capital has no
  coordinates: dropping one country shortens the response and hands every
  country after the gap its neighbour's weather, with nothing red anywhere. The
  fixture is recorded for all 41 for the same reason; a subset cannot be
  read back.
- **Asking past the archive's end is a 400, not an empty response.** The
  boundary sat at exactly *yesterday* when measured, so a bare `today - 1` fails
  on whichever side of the server's rollover a run lands. `WEATHER_END_LAG_DAYS`
  is the slack, and it is a correctness requirement rather than politeness —
  unlike the FX resource, which can ask for a weekend and get nothing back.
- **A 429 carries no `Retry-After`, only a sentence naming the window.** The
  three windows want waits three orders of magnitude apart, so the reason string
  is read (`weather_retry_after`). The daily one deliberately **raises** instead
  of sleeping: waiting out 24 hours inside a run is not a backoff, it is
  indistinguishable from a hang. `_get_json`'s 1.5s/3s escalation would burn all
  three retries in 4.5 seconds against the shortest of them — which is exactly
  how the first draft of `record_weather` failed, having spent the budget it
  needed on the way.
- **The watermark is read from the destination table, not from dlt state, and
  everything else depends on that.** `wb_wdi` and `ecb_fx_rates` keep theirs in
  `dlt.current.resource_state()`, i.e. in `~/.dlt` — a directory CI does not
  have. Their state is therefore empty on every workflow run and they re-ask for
  their whole series, which is free for them and would cost this one a fortnight
  of allowance. Carrying rows forward between releases only saves anything if
  the watermark travels *with the rows*, and the rows are all a published DuckDB
  file can carry. Making the data its own watermark also removes the second
  place it could be wrong.
- **Carrying the archive forward is what makes it deepen, and it now travels as
  its own release asset.** `raw` lives in the DuckLake catalog under
  `data/lakehouse/`, not in `data/warehouse.duckdb`, so the release publishes
  `lakehouse.tar.gz` beside the database and `scripts/restore_history.py`
  restores both. Verified end to end: restore a release into an empty tree and
  `weather_watermark()` reads the last day it carried, so the next ingest asks
  for a 90-day lookback rather than a three-year cold start.
  - **Only this table is published, and the allowlist has two independent
    reasons.** Cost — nothing else in `raw` is unreproducible within the budget.
    Disclosure — `raw.retail_invoice_lines` and dlt's `raw_staging` copy hold
    824,364 clear customer ids between them. And it cannot be filtered after the
    fact: DuckLake keeps dropped tables in earlier snapshots, so `at (version =>
    …)` still returns them (measured, with a customer id in it). The published
    catalog is *built* from `PUBLISHED_TABLES`, never trimmed down to it.
- **This makes `raw.om_weather_daily` the second table a rebuild cannot
  reproduce, for a new reason.** `history.snap_co2_estimates` is unreproducible
  in *principle* — a snapshot is state. This one is unreproducible within a
  *budget*, which is a weaker claim with the same consequence — so it is carried
  forward by the same mechanism, and the guards that named one schema were
  generalised to a tuple of `Carry` rules rather than duplicated (`just clean
  warehouse`'s gate, `release-data.yml`'s restore step and its "did not shrink"
  verify all count through `irreplaceable_rows()` now).
  - **Carrying it only works where dlt has no local state, and that is why the
    restore refuses rather than tries.** Landing it into `raw` creates that
    schema; a fresh runner then finds no `_dlt_version`, treats the dataset as
    new and merges onto the carried rows — 44,936 of them, measured. A machine
    that has run `just ingest` trusts its own state instead and dies with
    `Table with name _dlt_version does not exist!`. Carrying dlt's bookkeeping
    along is worse, not better: dlt then expects every table the schema
    describes, and `ecb_fx_rates` is the one that fails. `sync_destination()`
    does not help. `scripts/restore_history.py` refuses when both conditions
    hold and names `rm -rf ~/.dlt/pipelines/modern_data_stack` as the fix.
- **The 90-day lookback is why weather is the table a revision log is about.**
  Nothing else in this warehouse restates: FX is append-only, retail is frozen at
  2011-12, and `raw.owid_co2` has produced zero observed revisions locally.
  Re-merging 41 x 90 = 3,690 rows in place every ingest, on the *scheduled*
  ERA5T-to-ERA5 supersession, is the one real update path here.
  - **DuckLake's own change feed cannot report it, because of dlt.** dlt
    regenerates `_dlt_id` *and* `_dlt_load_id` on every row it re-merges,
    byte-identical weather or not — measured by reloading 500 identical rows,
    which returned 500 `update_preimage`/`update_postimage` pairs. So
    `ducklake_table_changes()` answers the same thing for a no-op reload and a
    real restatement. `lake.lakehouse.revisions()` diffs two snapshots with
    `EXCEPT` instead, projecting those columns away: 0 rows for the reload, 1 for
    the change.
  - **The failure is a plausible number, not an error.** Forget a provenance
    column and the diff reports the whole table, which reads as a catastrophic
    upstream restatement. `weather_revisions_are_derivable` is bounded on the
    total for that reason, and `tests/test_lakehouse.py` asserts the zero as hard
    as the one.
- **A cold start fetches three years, not the whole series, and getting that
  wrong is a *hang* rather than a failure.** `WEATHER_FIRST_YEAR` (2007) is the
  backfill floor; `WEATHER_COLD_START_YEARS` is what an unpartitioned load asks
  for when the destination is empty — which is the normal state of a fresh clone
  and of `pages.yml`, `nightly.yml` and `release-data.yml`, all three of which
  build from nothing against the live APIs. Starting a cold load at 2007 is the
  intuitive choice and costs ~12,600 units against a 10,000-a-day allowance; the
  limiter honours that allowance by **waiting**, so nothing errors. Simulated end
  to end with the injected clock: **24.1 hours, including one 22-hour sleep**.
  Three years is ~1,700 units and two minutes, and still yields two *complete*
  calendar years, which is the floor for the year-over-year comparison the mart
  exists for.
  - **Every per-window assertion passed throughout.** Each of the twenty
    requests was individually affordable and
    `weather_windows_chunk_the_seed_into_affordable_requests` was green — the
    bound that was missing is over the *total*. A per-item check cannot see a
    budget that only twenty items together exceed, which generalises past this
    source.
  - **The limiter is what converted the failure into a hang, and that is the
    worse direction.** Without pacing, the sixteenth request would have 429'd
    naming the daily window and `weather_retry_after` would have raised. Correct
    pacing turned a loud stop into a silent one; the guard has to be the test,
    because the runtime has no way left to complain.

- **The 90-day merge lookback is sized to ERA5T, not to politeness.**
  Open-Meteo serves preliminary ERA5T within a day or two of real time and
  Copernicus supersedes it with final ERA5 two to three months later. FX's ten
  days would freeze preliminary numbers *permanently* here, because rows outside
  the window are carried forward rather than refetched.
- **`wb_wdi` and `om_weather_daily` share one `@dlt_assets` block, and that is
  required.** `full_refresh` is `AssetSelection.all()` minus two things, so it
  contains both, and `define_asset_job` resolves a selection to a single
  `partitions_def` or raises. Two yearly definitions differing only in start year
  would break the job three workflows execute. The cost is that ERA5's 1940-1959
  is not addressable as a partition, since 1960 is the World Bank's floor — the
  right way round, because the alternative creates twenty WDI partitions that
  load nothing. Giving the block a second resource is also what made
  `raw_year_partitioned_assets` need `context.selected_asset_keys`; with one
  resource in the tuple, ignoring the selection was a no-op.
- **A capital is a coarse proxy and the model says so with a number.**
  `grid_distance_km` is the great-circle distance from the capital to the ERA5
  cell that answered — the API snaps to the nearest cell centre and reports where
  it landed, so Berlin's 52.5235/13.4115 comes back 52.54833/13.407822.
  Comparing a country with *itself* across years is what degree days are for
  here; comparing countries with each other is much weaker, and a
  population-weighted average over many cells is the honest version at many times
  the budget.
- **Degree days ship in two conventions on purpose.** `hdd_c` uses the daily
  mean; `hdd_minmax_c` uses (max + min)/2, which is what a station series reports
  because it is what a max/min thermometer records. Neither is more correct and
  they disagree on asymmetric days. The base temperature is a `var` carried on
  every row, for `dim_date.fiscal_year_start_month`'s reason exactly: it is a
  policy, the warehouse builds one value of it, and every other value it claims
  to support is untested by construction.
  - **"Disagree" is the whole of it — there is no ordering between them, and
    `_country_stats.yml` asserted one for three weeks.** A comment there claimed
    the midpoint convention "runs warmer than the mean-based one, never colder", by
    construction. Measured over the full archive (656 rows, 41 capitals x 16
    years): `hdd_minmax_total` is the **larger in 253 rows (38.6%)** and the
    smaller in 403, gaps running -153.0 to +96.2. Whether the midpoint sits
    above or below the true daily mean depends on the day's diurnal shape. So
    the claim is not merely unproven, it is false, and writing it into a data
    test turns the build red on reality — do not "fix" the comment by encoding
    it.
  - **The two being swapped is therefore uncatchable by a data test**, which is
    what the vacuous expression under that comment was pretending to do: swap
    them in the mart's final SELECT and all 29 of `fct_country_weather_year`'s
    data tests pass. It is a
    unit test now — `weather_year_keeps_the_two_degree_day_conventions_apart`,
    whose fixture deliberately puts one country on each side of the gap. See
    `unit-testing-dbt-models`.
- **The payoff is a negative result, which is the kind this warehouse could not
  previously reach.** Heating degree days for six EU capitals, 2021 against 2022:
  every one milder, inside a 6.5-point band (Germany -13.5%, Spain -10.2%,
  France -16.5%, Italy -12.0%, Netherlands -13.8%, Poland -10.0%) while
  electricity prices spread 81.8 points in both directions. Weather explains
  essentially none of the divergence, which upgrades the existing Netherlands
  finding — EUR 0.034 to EUR 0.142 across the 2022 halves, "a price nobody paid"
  — from narrated to demonstrated.
