"""Open-Meteo ERA5: daily weather for EU/EEA capitals.

The largest source module here, and the only one bounded by a *budget* rather
than by what the API will serve: Open-Meteo charges weighted units, so what can
be fetched in a run is finite and the rate limiter is part of the source rather
than a nicety.

Capital coordinates come from `ingest.sources.worldbank.country_pages`.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import dlt
import requests
from dlt.common.schema.typing import TColumnSchema

from ingest import fixtures, http
from ingest.sources import worldbank
from modern_data_stack import db
from modern_data_stack.ratelimit import WeightedWindowLimiter

# Open-Meteo's ERA5 reanalysis archive: daily weather at any point on Earth back
# to 1940, no key, no quota registration. https://open-meteo.com/en/docs/historical-weather-api
#
# **This is the source that reads a join key the warehouse already had and never
# used.** `stg_country` carries the World Bank's capital-city latitude/longitude
# for 211 of 228 countries, mentioned in this file until now only as a `try_cast`
# gotcha — and every keyless geospatial API joins on it with no new dimension and
# no bridge table.
OPEN_METEO_ARCHIVE_API = "https://archive-api.open-meteo.com/v1/archive"

# Scope: the 41 countries Eurostat reports an electricity price for. Not a
# rounding-down of "all 211 capitals" but the set the analysis actually joins to
# — `fct_eu_electricity_prices_semiannual` covers exactly these, all 41 carry
# coordinates, and "the column is null outside the EU/EEA" is already this
# warehouse's documented convention for that mart. `tests/test_ingest.py` holds
# the list to the price data rather than to a hand-written comment.
WEATHER_COUNTRIES = (
    "ALB", "AUT", "BEL", "BGR", "BIH", "CYP", "CZE", "DEU", "DNK", "ESP",
    "EST", "FIN", "FRA", "GBR", "GEO", "GRC", "HRV", "HUN", "IRL", "ISL",
    "ITA", "LIE", "LTU", "LUX", "LVA", "MDA", "MKD", "MLT", "MNE", "NLD",
    "NOR", "POL", "PRT", "ROU", "SRB", "SVK", "SVN", "SWE", "TUR", "UKR",
    "XKX",
)  # fmt: skip

# The API's own daily variable names, kept verbatim in `raw` the way every other
# landing table here keeps what the publisher sent. Renaming and unit-carrying
# happen in staging. Three temperatures because degree days can be computed two
# defensible ways — from the mean, or from (max + min) / 2 — and holding both
# makes that a *tested* choice rather than an asserted one; the other three open
# the weather-against-renewables question the energy columns already invite.
WEATHER_DAILY_VARIABLES = (
    "temperature_2m_mean",
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "wind_speed_10m_max",
    "shortwave_radiation_sum",
)

# The units the API declares for those six, checked against a live response by
# `tests/test_ingest.py` rather than trusted. A silent unit change upstream would
# move every derived figure by a constant and break no range test.
WEATHER_EXPECTED_UNITS = {
    "temperature_2m_mean": "°C",
    "temperature_2m_max": "°C",
    "temperature_2m_min": "°C",
    "precipitation_sum": "mm",
    "wind_speed_10m_max": "km/h",
    "shortwave_radiation_sum": "MJ/m²",
}

# The earliest year worth asking for: 2007 is the first year Eurostat publishes
# an electricity price, so it is the floor of the span the payoff joins to. ERA5
# itself reaches back to 1940 and `raw/om_weather_daily` is year-partitioned, so
# this is not a limit either — `just backfill-weather 1997 2006` goes deeper
# whenever someone decides to spend the budget.
#
# **It is deliberately not where a cold start begins.** See
# `WEATHER_COLD_START_YEARS`.
WEATHER_FIRST_YEAR = 2007

# How much history an *unpartitioned* load fetches when the destination is empty.
#
# This is the constant that keeps a fresh clone usable, and it exists because the
# obvious alternative is a trap. Starting a cold load at `WEATHER_FIRST_YEAR`
# reads as the generous choice — 2007 onwards, the whole useful span — and costs
# ~12,600 units against a 10,000-a-day allowance. The limiter honours that
# allowance by *waiting*, so the load does not fail: it paces for two hours, then
# sleeps for twenty-two more. Simulated end to end at **24.1 hours with a single
# 22-hour sleep**, which is a hang wearing a progress bar, and it would have hit
# `pages.yml`, `nightly.yml` and `release-data.yml` — all three run against the
# live APIs from an empty warehouse.
#
# Three years is ~1,700 units: inside the hourly budget, so a cold start is a
# couple of minutes, and it still gives two *complete* calendar years, which is
# the minimum for the year-over-year comparison the mart exists for. Depth beyond
# that arrives the way the design always intended — carried forward from the
# previous release, or asked for explicitly with a partition key.
#
# `tests/test_ingest.py` holds the cold start to the hourly budget, so the
# generous-looking edit fails a test rather than a workflow.
WEATHER_COLD_START_YEARS = 3

# How far back an incremental run re-asks for, and it is deliberately much longer
# than FX's ten days. ECB fixings are never restated; ERA5 *is*. Open-Meteo
# serves preliminary ERA5T within a day or two of real time and Copernicus
# supersedes it with final ERA5 two to three months later, so a window shorter
# than that would freeze preliminary numbers into the warehouse permanently —
# and because rows outside the window are carried forward between releases
# rather than refetched, "permanently" here means exactly that.
WEATHER_LOOKBACK_DAYS = 90

# Asking past the archive's last day is a **400, not an empty response** —
# `{"reason": "Parameter 'end_date' is out of allowed range from 1940-01-01 to
# …"}`. The boundary sat at exactly yesterday when this was measured, so a bare
# `today - 1` would fail on whichever side of the server's rollover a run
# happened to land. Three days is slack, and costs three days of latency on a
# series whose consumers are annual.
WEATHER_END_LAG_DAYS = 3

WEATHER_PRIMARY_KEY = ("country_iso3", "weather_date")

# Open-Meteo's published free-tier budget: 600 units a minute, 5,000 an hour,
# 10,000 a day (a monthly 300,000 exists and is not yet enforced). Units are not
# requests — see `weather_call_units`.
WEATHER_RATE_LIMITS = ((60.0, 600.0), (3600.0, 5000.0), (86400.0, 10000.0))

# A 429 from this API carries **no `Retry-After` header**. What it does carry is
# a message naming *which* window was exceeded — "Minutely API request limit
# exceeded", "Hourly …", "Daily …" — and those want waits three orders of
# magnitude apart, so the reason string is the only signal there is and it is
# worth reading. `http.get_json`'s 1.5s/3s backoff would burn all three of its
# retries in 4.5 seconds against the shortest of them.
#
# The daily window is deliberately absent: waiting out a day inside a pipeline
# run is not a retry, it is a hang. That one raises and says to come back
# tomorrow or narrow the window.
WEATHER_RETRY_AFTER_SECONDS = {"minutely": 65.0, "hourly": 660.0}
WEATHER_DEFAULT_RETRY_AFTER_SECONDS = 65.0
WEATHER_RETRIES = 6

# The wait for a failure that is *not* a rate limit — a 5xx, a reset connection,
# a truncated body. `http.get_json`'s own backoff, deliberately: those failures have
# nothing to do with the budget, so they should not inherit the minute-long wait
# a 429 earns. Only the 429 path reads `WEATHER_RETRY_AFTER_SECONDS`.
WEATHER_BACKOFF_SECONDS = 1.5

# Declared rather than inferred, for the third time in this file and the same
# reason: a merge resource keeps dlt's persisted schema, which only widens. The
# six measurements are the ones that matter — a window that happened to hold only
# whole millimetres of rain would infer bigint for `precipitation_sum` and shunt
# the next 0.2 into a `precipitation_sum__v_double` variant column.
WEATHER_COLUMNS: dict[str, TColumnSchema] = {
    "country_iso3": {"data_type": "text", "nullable": False},
    "weather_date": {"data_type": "date", "nullable": False},
    # The ERA5 grid cell the request actually landed in, which is *not* the
    # capital's coordinates — the API snaps to the nearest cell centre and says
    # so in its response (Berlin's 52.5235/13.4115 comes back 52.54833/13.407822).
    # Carried so the staging layer can state the distance rather than imply none.
    "grid_latitude": {"data_type": "double"},
    "grid_longitude": {"data_type": "double"},
    "elevation_m": {"data_type": "double"},
    "temperature_2m_mean": {"data_type": "double"},
    "temperature_2m_max": {"data_type": "double"},
    "temperature_2m_min": {"data_type": "double"},
    "precipitation_sum": {"data_type": "double"},
    "wind_speed_10m_max": {"data_type": "double"},
    "shortwave_radiation_sum": {"data_type": "double"},
}


def weather_call_units(locations: int, days: int, variables: int | None = None) -> float:
    """What one archive request costs against `WEATHER_RATE_LIMITS`.

    Open-Meteo prices a request by *volume*, not by call: its documentation gives
    `(variables / 10) * (days / 14) * locations`, and the charge lands after the
    response rather than before it. That is why a single oversized request
    succeeds and the next one is refused — measured, not inferred: one 86-year
    three-variable request costs ~673 units against a 600/minute budget and is
    served, then everything for the next minute is a 429.

    The practical consequence is that the whole shape of this resource is upside
    down from `wb_wdi`'s. There, eight threads fetch eleven indicators at once
    because the only cost is latency. Here the cost is shared and finite, so the
    fetch is a single paced loop and a thread pool is precisely what would break
    it.
    """
    if variables is None:
        variables = len(WEATHER_DAILY_VARIABLES)
    return (variables / 10) * (days / 14) * locations


def _coordinate(value: object) -> float | None:
    """One World Bank coordinate as a float — the API sends `''` for territories."""
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def weather_locations() -> list[tuple[str, float, float]]:
    """`(country_iso3, latitude, longitude)` for the countries in scope, sorted.

    Read from the World Bank `/country` payload rather than from
    `staging.stg_country`, which is where these coordinates are *modelled*. Three
    reasons, and the third is the one that settles it:

    * **Ordering.** dbt builds staging from `raw`, so a staging read would make
      this resource depend on a table that does not exist during a first load.
    * **Dagster.** `raw/wb_country` and this asset are both roots with no edge
      between them, so nothing would guarantee the country table landed first —
      and an empty location list is a load of zero rows, which fails green.
    * **The recorder.** `scripts/record_fixtures.py` has no warehouse at all, and
      a fixture recording that needs a built database is a fixture recording
      nobody can reproduce from a clone.

    Going through `http.get_json` means this is fixture-aware for free, so an offline
    run resolves the same coordinates CI recorded.

    **Fails closed.** A country in scope with no coordinates raises rather than
    being dropped: silently narrowing the request would produce a shorter
    response, and the response is matched to the request *by position*.
    """
    wanted = set(WEATHER_COUNTRIES)
    found: dict[str, tuple[float, float]] = {}
    for rows in worldbank.country_pages():
        for row in rows:
            iso3 = row.get("id")
            if iso3 not in wanted or iso3 in found:
                continue
            latitude, longitude = (
                _coordinate(row.get("latitude")),
                _coordinate(row.get("longitude")),
            )
            if latitude is not None and longitude is not None:
                found[iso3] = (latitude, longitude)

    missing = sorted(wanted - set(found))
    if missing:
        raise RuntimeError(
            f"no capital coordinates for {', '.join(missing)} — the weather request is "
            "matched to its response by position, so a short list would mislabel every "
            "country after the gap rather than lose one"
        )
    return [(iso3, *found[iso3]) for iso3 in sorted(found)]


def weather_url(
    locations: Sequence[tuple[str, float, float]], start_date: str, end_date: str
) -> str:
    """The archive request URL for every location over one date window.

    Every location travels in **one** request, comma-separated. That is not a
    tidiness choice: weight is charged partly per request, so five locations in
    one call cost far less than five calls — measured at the point where one
    86-year single-location request was being refused while a five-location one
    of the same span was served.

    Coordinates are formatted to four decimals so the URL is byte-stable for a
    given location set, which is what makes a recorded fixture reproducible. The
    precision is free: ERA5's grid is 0.25°, so the API snaps to a cell centre
    that four decimals cannot change.
    """
    latitudes = ",".join(f"{latitude:.4f}" for _, latitude, _ in locations)
    longitudes = ",".join(f"{longitude:.4f}" for _, _, longitude in locations)
    return (
        f"{OPEN_METEO_ARCHIVE_API}?latitude={latitudes}&longitude={longitudes}"
        f"&start_date={start_date}&end_date={end_date}"
        f"&daily={','.join(WEATHER_DAILY_VARIABLES)}&timezone=UTC"
    )


def weather_end_date(today: date | None = None) -> str:
    """The last day worth asking for — see `WEATHER_END_LAG_DAYS`."""
    day = today or datetime.now(UTC).date()
    return (day - timedelta(days=WEATHER_END_LAG_DAYS)).isoformat()


def weather_start_date(last_loaded_date: str | None, today: date | None = None) -> str:
    """The first day the next unpartitioned load should ask for.

    Two branches, and the empty one is the load-bearing half:

    * **Nothing loaded yet** — `WEATHER_COLD_START_YEARS` of history, *not*
      `WEATHER_FIRST_YEAR`. An empty destination is the normal state of a fresh
      clone and of all three live workflows, so this branch runs far more often
      than the other one, and reaching back to 2007 here costs more than a day's
      allowance. See the constant for the simulated cost of getting this wrong.
    * **A watermark exists** — `WEATHER_LOOKBACK_DAYS` back from it, clamped at
      `WEATHER_FIRST_YEAR` so a watermark near the floor cannot ask for years
      before the series is worth having.

    Deep history is deliberately not reachable from either branch: it is carried
    forward from the previous release, or asked for with a partition key.
    """
    floor = date(WEATHER_FIRST_YEAR, 1, 1)
    if last_loaded_date is None:
        day = today or datetime.now(UTC).date()
        return max(date(day.year - WEATHER_COLD_START_YEARS + 1, 1, 1), floor).isoformat()
    start = date.fromisoformat(str(last_loaded_date)) - timedelta(days=WEATHER_LOOKBACK_DAYS - 1)
    return max(start, floor).isoformat()


def weather_watermark(lakehouse_dir: str | Path | None = None) -> str | None:
    """The newest day already in `raw.om_weather_daily`, or None if there is none.

    **Read from the destination, not from dlt's resource state**, which is the
    one design decision here that everything else depends on. `wb_wdi` and
    `ecb_fx_rates` keep their watermarks in `dlt.current.resource_state()`, which
    lives in `~/.dlt` — a directory CI does not have. So on every workflow run
    dlt's state is empty and those resources re-ask for their whole series, which
    is free for them and would cost this one a fortnight of API budget.

    Carrying the table forward between releases only saves anything if the
    watermark travels *with the rows*. Making the data its own watermark is also
    strictly more honest: there is no second place for it to be wrong.

    **The destination is now the lakehouse**, which changes what "carry the rows
    forward" means: it is the DuckLake catalog and its Parquet that a release has
    to ship, not a schema inside the published DuckDB file. Ship neither and this
    returns None on every workflow run, and every release cold-starts the archive
    at `WEATHER_COLD_START_YEARS` — silently, because a cold start is a valid
    state and not an error.

    A missing catalog is "nothing loaded yet". A catalog that cannot be *read* is
    not — that raises, because falling back would silently re-seed from 2007.
    """
    from lake.lakehouse import LAKEHOUSE_DIR, catalog_path, read_only_connection

    lake = Path(lakehouse_dir if lakehouse_dir is not None else LAKEHOUSE_DIR)
    if not catalog_path(lake).exists():
        return None
    con = read_only_connection(lake)
    try:
        present = db.scalar(
            con,
            """
            select count(*) from information_schema.tables
            where table_schema = 'raw' and table_name = 'om_weather_daily'
            """,
        )
        if not present:
            return None
        newest = db.scalar(con, "select max(weather_date) from lakehouse.raw.om_weather_daily")
        return None if newest is None else str(newest)
    finally:
        con.close()


def weather_windows(
    years: tuple[int, int] | None = None,
    watermark: str | None = None,
    today: date | None = None,
) -> list[tuple[str, str]]:
    """The `(start, end)` date windows to request, one per calendar year.

    Split by year for two reasons that happen to agree. One request per year for
    all 41 locations is ~641 units, which sits just above the per-minute budget
    and well inside the hourly one — small enough to pace, large enough that the
    per-request overhead disappears. And a calendar year is exactly what
    `raw/om_weather_daily`'s Dagster partition is, so the backfill path and the
    incremental path chunk the work identically.

    `years` is the backfill window, asked for verbatim and clipped to the
    archive's end. Without it the window runs from the incremental start date.
    """
    last = weather_end_date(today)
    if years is None:
        first = weather_start_date(watermark, today)
    else:
        first = date(years[0], 1, 1).isoformat()
        last = min(last, date(years[1], 12, 31).isoformat())
    if first > last:
        return []

    windows = []
    for year in range(int(first[:4]), int(last[:4]) + 1):
        start = max(first, date(year, 1, 1).isoformat())
        end = min(last, date(year, 12, 31).isoformat())
        windows.append((start, end))
    return windows


def weather_retry_after(reason: str) -> float:
    """How long to wait after a 429, read off the message naming the window.

    Raises for the daily window: 24 hours is not a backoff, and a pipeline that
    slept through one would look identical to a hung one for a day.
    """
    lowered = reason.lower()
    if "daily" in lowered:
        raise RuntimeError(
            f"Open-Meteo's daily budget is spent: {reason.strip()[:200]} — this is not "
            "something to wait out inside a run. Re-run tomorrow, or narrow the window "
            "with `just backfill-weather <start> <end>`."
        )
    for window, seconds in WEATHER_RETRY_AFTER_SECONDS.items():
        if window in lowered:
            return seconds
    return WEATHER_DEFAULT_RETRY_AFTER_SECONDS


def get_weather_json(url: str, limiter: WeightedWindowLimiter, units: float) -> dict | list:
    """GET one archive window, spending `units` of a shared budget to do it.

    Under fixtures there is no budget to keep and nothing to sleep for, so this
    delegates straight to `http.get_json` — a paced fixture run would otherwise make
    CI wait out a rate limit that no request is being made against.

    The limiter paces what *this process* has spent, which is all it can know.
    Anything else that touched the API in the same hour — a previous run, the
    fixture recorder, a person with curl — is invisible to it, so a 429 is not a
    bug in the pacing but the one signal that the budget is shared. That is why
    the retry exists at all, and why its wait comes from the response rather than
    from a constant: see `weather_retry_after`.

    The units are charged whether or not the response was useful. The API counted
    the request, so this has to as well; charging only on success would make the
    limiter optimistic exactly when it is already behind.

    **Three outcomes, and only one of them is the rate limit.** A 429 waits what
    the message says. A 5xx, a timeout, a reset or an unparseable body is the
    transient class every other source here already retries through `http.get_json`,
    and gets `WEATHER_BACKOFF_SECONDS`. Any other 4xx is raised immediately,
    because it is deterministic — a retry buys the same rejection three more
    times and spends the budget doing it.
    """
    if fixtures.enabled():
        return http.get_json(url)

    last: Exception | None = None
    for attempt in range(WEATHER_RETRIES):
        limiter.acquire(units)
        wait = WEATHER_BACKOFF_SECONDS * (attempt + 1)
        try:
            try:
                resp = requests.get(url, timeout=300)
            finally:
                # Charged once per attempt, in a `finally` so a timeout or a
                # reset counts too. The API bills what it served, and a
                # connection that died on our side says nothing about whether it
                # served — so an uncertain spend counts, for the same reason the
                # docstring gives for charging a useless response.
                limiter.charge(units)

            if resp.status_code == 429:
                # `weather_retry_after` raises for the daily window rather than
                # returning a wait, and that raise is meant to escape this loop.
                wait = weather_retry_after(resp.text)
                last = RuntimeError(f"rate limited: {resp.text[:200]}")
            else:
                resp.raise_for_status()
                return resp.json()
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status < 500:
                # A 4xx that is not a 429 is the server rejecting *this request*,
                # and the next identical one is rejected identically while costing
                # the same units. The live example is the archive's end trailing
                # real time by more than `WEATHER_END_LAG_DAYS`, which answers
                # `400 Parameter 'end_date' is out of allowed range`: that wants
                # the constant moved, not six retries and six charges against a
                # shared budget. Raising here says which request was wrong.
                raise
            last = exc
        except (requests.RequestException, ValueError) as exc:  # ValueError = JSONDecodeError
            # The class every other source in this file already retries through
            # `http.get_json` — a timeout, a reset, an error page that is not JSON.
            # Weather did not, so one transient failure anywhere in a backfill
            # killed the whole load and lost the windows already paid for.
            last = exc

        if attempt == WEATHER_RETRIES - 1:
            break
        time.sleep(wait)
    raise RuntimeError(f"failed to fetch weather from {url[:120]}…: {last}")


def _weather_rows(
    payload: dict | list, locations: Sequence[tuple[str, float, float]]
) -> Iterable[dict]:
    """Flatten one archive response into `(country, day)` rows.

    Open-Meteo answers column-wise — `{"daily": {"time": [...],
    "temperature_2m_mean": [...]}}` — which is the cheapest possible shape on the
    wire and has to be transposed here so the landing table is at the grain the
    merge key is defined on. Same reasoning as `ecb_fx_rates` unpivoting its wide
    payload rather than landing a column per currency.

    **The response is matched to the request by position, and there is no other
    way to do it.** A multi-location request returns a JSON *array*, and the
    entries carry a `location_id` — except the first, which has none at all
    (they run absent, 1, 2, …). So the only reliable key is the index, which is
    why `weather_locations` is sorted and fails closed rather than dropping a
    country: a short response would silently shift every country after the gap
    onto the wrong weather.
    """
    entries = payload if isinstance(payload, list) else [payload]
    if len(entries) != len(locations):
        raise RuntimeError(
            f"asked Open-Meteo for {len(locations)} locations and got {len(entries)} back — "
            "the response is matched by position, so this cannot be resolved here"
        )

    for (country_iso3, _, _), entry in zip(locations, entries):
        daily = entry.get("daily") or {}
        days = daily.get("time") or []
        columns = {name: daily.get(name) or [] for name in WEATHER_DAILY_VARIABLES}
        for index, day in enumerate(days):
            measured = {
                name: (values[index] if index < len(values) else None)
                for name, values in columns.items()
            }
            # **A day with nothing measured on it is dropped, and the disposition
            # is why.** This resource `merge`s on `(country_iso3, weather_date)`
            # and the ingest layer re-asks for the last 90 days on every run, so
            # a row here does not add to what is stored — it *replaces* the row
            # already at that key. The preliminary ERA5T tail answers a day it
            # has not finished with as a present `time` entry whose variables are
            # all null, so emitting it would overwrite a day that landed complete
            # on an earlier run with a row of nulls. No error, no range test to
            # fail: the archive would simply get shallower in places, and only
            # the `not_null` tests in `stg_weather_daily` would say so.
            #
            # The residual is worth stating rather than papering over: dlt merges
            # whole rows, so a *partially* null day still replaces a fuller one.
            # There is no column-level merge to reach for. What this rules out is
            # the case that costs everything for nothing.
            if all(value is None for value in measured.values()):
                continue
            yield {
                "country_iso3": country_iso3,
                "weather_date": day,
                "grid_latitude": entry.get("latitude"),
                "grid_longitude": entry.get("longitude"),
                "elevation_m": entry.get("elevation"),
                **measured,
            }


@dlt.resource(
    name="om_weather_daily",
    write_disposition="merge",
    primary_key=WEATHER_PRIMARY_KEY,
    columns=WEATHER_COLUMNS,
)
def om_weather_daily(years: tuple[int, int] | None = None):
    """Daily ERA5 weather at each capital city — the warehouse's first live join
    on a coordinate, and the first table here a rebuild cannot afford to reproduce.

    `history.snap_co2_estimates` is unreproducible in *principle*: a snapshot is
    state and no rebuild can invent a revision. This one is unreproducible within
    a *budget*, which is a different thing and a weaker claim, but it has the same
    consequence — 41 capitals over 2007-2025 at six variables costs about 12,200
    of Open-Meteo's units against a 10,000-a-day allowance, so a workflow that
    re-fetched it from scratch every run would spend a day and a half of the
    project's quota to arrive back where it started. The rows are therefore
    carried forward from the previous release and this resource only ever asks
    for what is new, which is what `weather_watermark` is for.

    Two paths into the same load, exactly as `wb_wdi` has:

    * **partitioned** — an explicit `(first, last)` year range, which is what a
      Dagster backfill or `just backfill-weather` sends. Loads those years and
      leaves the watermark alone, because a run over one window cannot claim
      everything up to its end is present.
    * **unpartitioned** — from the watermark's lookback window to the archive's
      end, which is what the schedule, CI and the release workflow run. On an
      *empty* destination that window is `WEATHER_COLD_START_YEARS`, not the
      whole series: an empty warehouse is the normal state of a fresh clone and
      of all three live workflows, so this is the common path rather than the
      rare one, and asking it for twenty years costs more than a day's
      allowance.
    """
    locations = weather_locations()
    limiter = WeightedWindowLimiter(WEATHER_RATE_LIMITS)
    watermark = None if years is not None else weather_watermark()

    windows = weather_windows(years, watermark)
    if fixtures.enabled() and windows:
        # One request instead of one per year. The recorded payload is the same
        # file whatever window is asked for — the route captures no dates — so
        # chunking offline would re-parse one fixture nineteen times to land the
        # rows it landed the first time. The chunking itself is what
        # `tests/test_ingest.py` covers directly, and it exists to pace a budget
        # that an offline run does not spend.
        windows = [(windows[0][0], windows[-1][1])]

    for start, end in windows:
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
        payload = get_weather_json(
            weather_url(locations, start, end),
            limiter,
            weather_call_units(len(locations), days),
        )
        yield list(_weather_rows(payload, locations))
