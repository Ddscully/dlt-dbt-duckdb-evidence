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
# to 1940, no key. https://open-meteo.com/en/docs/historical-weather-api
OPEN_METEO_ARCHIVE_API = "https://archive-api.open-meteo.com/v1/archive"

# The 41 countries Eurostat reports an electricity price for — the set
# `fct_eu_electricity_prices_semiannual` covers and the weather analysis joins
# to. `tests/test_ingest.py` holds the list to the price data.
WEATHER_COUNTRIES = (
    "ALB", "AUT", "BEL", "BGR", "BIH", "CYP", "CZE", "DEU", "DNK", "ESP",
    "EST", "FIN", "FRA", "GBR", "GEO", "GRC", "HRV", "HUN", "IRL", "ISL",
    "ITA", "LIE", "LTU", "LUX", "LVA", "MDA", "MKD", "MLT", "MNE", "NLD",
    "NOR", "POL", "PRT", "ROU", "SRB", "SVK", "SVN", "SWE", "TUR", "UKR",
    "XKX",
)  # fmt: skip

# The API's own variable names, kept verbatim in `raw`; staging renames. Three
# temperatures because degree days have two conventions — from the mean, or from
# (max + min) / 2 — and staging computes both.
WEATHER_DAILY_VARIABLES = (
    "temperature_2m_mean",
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "wind_speed_10m_max",
    "shortwave_radiation_sum",
)

# The units the API declares for those six, checked by `tests/test_ingest.py`:
# an upstream unit change would shift every derived figure and fail no range test.
WEATHER_EXPECTED_UNITS = {
    "temperature_2m_mean": "°C",
    "temperature_2m_max": "°C",
    "temperature_2m_min": "°C",
    "precipitation_sum": "mm",
    "wind_speed_10m_max": "km/h",
    "shortwave_radiation_sum": "MJ/m²",
}

# The floor of a watermark-driven load: 2007 is Eurostat's first electricity
# price year. Not a hard limit — a partitioned backfill reaches back to 1960 —
# and not where a cold start begins (WEATHER_COLD_START_YEARS).
WEATHER_FIRST_YEAR = 2007

# How much history an unpartitioned load fetches into an empty destination, the
# state of a fresh clone and of all three live workflows. Starting at
# WEATHER_FIRST_YEAR instead costs ~12,600 units against 10,000 a day, and the
# limiter honours the daily window by sleeping — a ~22-hour hang, not a failure.
# Three years (~1,700 units) fits the hourly budget and still gives two complete
# years for a year-over-year comparison. Deeper history is carried from the
# previous release or backfilled. `tests/test_ingest.py` holds this to the
# hourly budget.
WEATHER_COLD_START_YEARS = 3

# How far back an incremental run re-asks. Open-Meteo serves preliminary ERA5T
# within days and Copernicus supersedes it with final ERA5 two to three months
# later; a shorter window would freeze preliminary values into the carried
# archive for good.
WEATHER_LOOKBACK_DAYS = 90

# Asking past the archive's last day is a 400, not an empty response, and that
# day sat at exactly yesterday when measured — so `today - 1` would fail on one
# side of the server's rollover. Three days of latency is free on annual use.
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

# Declared rather than inferred: a merge resource keeps dlt's persisted,
# widen-only schema, so a window of whole-millimetre rain would infer bigint for
# `precipitation_sum` and send the next 0.2 into a `__v_double` variant column.
WEATHER_COLUMNS: dict[str, TColumnSchema] = {
    "country_iso3": {"data_type": "text", "nullable": False},
    "weather_date": {"data_type": "date", "nullable": False},
    # The ERA5 grid cell the API snapped to, not the capital's coordinates
    # (Berlin's 52.5235/13.4115 comes back 52.54833/13.407822).
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

    Open-Meteo prices by volume — `(variables / 10) * (days / 14) * locations`
    per its documentation — and charges after serving, so one oversized request
    succeeds and the next minute is all 429s. Hence a single paced loop here
    rather than the thread pool the free-to-call sources use.
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

    Read from the World Bank `/country` payload, not `staging.stg_country`:
    staging does not exist during a first load, nothing orders `raw/wb_country`
    before this asset, and `scripts/record_fixtures.py` has no warehouse. Going
    through `http.get_json` also makes it fixture-aware.

    Fails closed: a country with no coordinates raises rather than being
    dropped, because the response is matched to the request by position.
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

    Every location travels in one request, comma-separated: measured, a
    five-location request was served where a single-location one of the same
    span was refused.

    Four decimals keep the URL byte-stable, so a recorded fixture is
    reproducible; ERA5's 0.25° grid makes the lost precision irrelevant.
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

    With no watermark, `WEATHER_COLD_START_YEARS` of history (not
    `WEATHER_FIRST_YEAR` — see the constant). With one, `WEATHER_LOOKBACK_DAYS`
    back from it, clamped at `WEATHER_FIRST_YEAR`. Deeper history comes from the
    previous release or a partition key, never from here.
    """
    floor = date(WEATHER_FIRST_YEAR, 1, 1)
    if last_loaded_date is None:
        day = today or datetime.now(UTC).date()
        return max(date(day.year - WEATHER_COLD_START_YEARS + 1, 1, 1), floor).isoformat()
    start = date.fromisoformat(str(last_loaded_date)) - timedelta(days=WEATHER_LOOKBACK_DAYS - 1)
    return max(start, floor).isoformat()


def weather_watermark(lakehouse_dir: str | Path | None = None) -> str | None:
    """The newest day already in `raw.om_weather_daily`, or None if there is none.

    Read from the destination, not dlt's resource state. `wb_wdi` and
    `ecb_fx_rates` keep theirs in `~/.dlt`, which a CI runner does not have, so
    every workflow run re-fetches their whole series — free for them, a fortnight
    of budget here. Carried rows bring their own watermark, provided the release
    ships the landing zone (`lakehouse.tar.gz`); without it every release
    cold-starts, silently.

    A missing catalog means nothing loaded yet. An unreadable one raises rather
    than falling back to a cold start.
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

    A year for all 41 locations is ~641 units — just over the per-minute budget,
    well inside the hourly one — and is the asset's partition, so backfill and
    incremental loads chunk identically.

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

    Under fixtures it delegates to `http.get_json`: there is no budget to pace.

    The limiter knows only this process's spend; a 429 means something else
    shared the budget (an earlier run, the recorder, a person with curl), so the
    wait is read off the response (`weather_retry_after`). Every attempt is
    charged, useful or not, because the API counts it.

    A 429 waits what its message says; a 5xx, timeout, reset or unparseable body
    backs off `WEATHER_BACKOFF_SECONDS`; any other 4xx raises at once, since a
    retry would get the same rejection and spend budget on it.
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
                # In a `finally` so a timeout or reset is charged too: a
                # connection that died here says nothing about whether the API
                # served, and so billed, the request.
                limiter.charge(units)

            if resp.status_code == 429:
                # Raises for the daily window, and that raise escapes the loop.
                wait = weather_retry_after(resp.text)
                last = RuntimeError(f"rate limited: {resp.text[:200]}")
            else:
                resp.raise_for_status()
                return resp.json()
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status < 500:
                # Deterministic: e.g. `end_date` past the archive's end wants
                # WEATHER_END_LAG_DAYS moved, not six charged retries.
                raise
            last = exc
        except (requests.RequestException, ValueError) as exc:  # ValueError = JSONDecodeError
            # Transient: a timeout, a reset, an error page that is not JSON.
            last = exc

        if attempt == WEATHER_RETRIES - 1:
            break
        time.sleep(wait)
    raise RuntimeError(f"failed to fetch weather from {url[:120]}…: {last}")


def _weather_rows(
    payload: dict | list, locations: Sequence[tuple[str, float, float]]
) -> Iterable[dict]:
    """Flatten one archive response into `(country, day)` rows.

    Open-Meteo answers column-wise (`{"daily": {"time": [...], ...}}`), so this
    transposes to the merge key's grain.

    Entries are matched to locations by position: a multi-location response is
    a JSON array whose `location_id` is absent on the first entry (absent, 1,
    2, …). A short response would shift every later country onto the wrong
    weather, which is why the length is checked and `weather_locations` fails
    closed.
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
            # Drop a day with nothing measured. The merge replaces the stored row
            # at this key, and the ERA5T tail returns unfinished days as all-null,
            # which would overwrite a complete day loaded earlier. A partly null
            # day still replaces a fuller one: dlt merges whole rows.
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
    """Daily ERA5 weather at each capital city.

    Unreproducible within a budget: 2007 to date for 41 capitals costs more than
    Open-Meteo's 10,000 daily units, so the rows are carried forward from the
    previous release and this asks only for what is new (`weather_watermark`).

    With `years` (a backfill, `just backfill-weather`) it loads exactly those
    years and ignores the watermark. Without, it loads from the watermark's
    lookback to the archive's end — or `WEATHER_COLD_START_YEARS` into an empty
    destination.
    """
    locations = weather_locations()
    limiter = WeightedWindowLimiter(WEATHER_RATE_LIMITS)
    watermark = None if years is not None else weather_watermark()

    windows = weather_windows(years, watermark)
    if fixtures.enabled() and windows:
        # One request: the fixture route ignores dates, so per-year chunks would
        # parse the same file once per year. `tests/test_ingest.py` covers the
        # chunking directly.
        windows = [(windows[0][0], windows[-1][1])]

    for start, end in windows:
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
        payload = get_weather_json(
            weather_url(locations, start, end),
            limiter,
            weather_call_units(len(locations), days),
        )
        yield list(_weather_rows(payload, locations))
