# 0002. WDI and weather take their backfill years as run config, not partitions

Status: accepted 2026-09-17 (#69)

## Context

`raw/wb_wdi` was a yearly partition from 2026-07-30, and `raw/om_weather_daily`
joined it when weather arrived.
The argument for them still holds: the API takes a date range, the disposition
is `merge`, and the year is in the primary key, so a year is a real, re-runnable
unit of work. That made a partition *possible*. Two things made it the wrong tool.

- **A job takes its assets' partitions definition, and a partitioned job's
  Materialize button is a backfill.** The dialog is a partition picker with no
  "no partition" choice. Read out of the 1.13.22 UI bundle: the default
  selection is empty, and when the selection's root assets carry different
  definitions it is fixed at "All partitions". Only the Launchpad runs a
  partitioned job plain. On 2026-09-13 the button launched `full_refresh` over
  1960–2026 as one run, cancelled after ten minutes with nothing red, where the
  same job from the Launchpad finished in 1m38s. Weather's share alone was
  ~42,800 units against Open-Meteo's 10,000 a day, which the limiter would have
  paced over days.
- **No routine run ever filled a partition.** The schedule, the live workflows
  and every recipe but the two backfills ran the lookback with no key, so
  partition status sat empty while `raw.wb_wdi` held the full series. A
  partition only ever did a backfill's job, which is what run config is for.

## Decision

Both sources take a `YearRange` run config on the `ingest_by_year` op. Unset, a
run loads the lookback; `first_year` (with `last_year`, defaulting to it) loads
that closed range. `just backfill-wdi` and `just backfill-weather` set it, and
from the UI it is the Launchpad. Retail stays partitioned by month, where every
partition together is one read of one file.

## Rejected

- **Keeping the partitions, with the Launchpad as the way to run the routine
  load.** It was the only way, and the button that looks like the routine load
  would still be a backfill of every year since 1960, and days of the weather
  budget.

## Consequences

- Nothing `BackfillPolicy.single_run()` gave the partitions is lost: WDI asks
  `&date=lo:hi`, so a range is still one request per indicator.
- `just materialize-select 'raw/wb_wdi*'` works. With the partitions, the CLI
  refused it with "Asset has partitions, but no '--partition' option was
  provided", while the README advertised it.
- A weather range over a day's allowance has no partition count to bound it, so
  `check_weather_range_is_affordable` refuses it before any request
  (`weather-models`).
- `tests/test_definitions.py` asserts that `full_refresh` and `publish_site`
  have no `partitions_def`, because one partitioned asset joining either
  selection would bring the picker back with nothing else red.
