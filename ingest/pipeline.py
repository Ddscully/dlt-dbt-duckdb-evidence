"""dlt ingestion: pull public CSV/JSON sources into the DuckDB warehouse.

Sources (all freely licensed; country + year keyed apart from the last):
  - OWID CO2 & GHG        https://github.com/owid/co2-data
  - OWID Energy           https://github.com/owid/energy-data
  - World Bank WDI        https://databank.worldbank.org/source/world-development-indicators
  - World Bank countries  https://api.worldbank.org/v2/country?format=json  (dimension table)
  - Eurostat prices       https://ec.europa.eu/eurostat/databrowser/view/nrg_pc_204  (EU only)
  - ECB reference rates   https://frankfurter.dev  (daily FX — the one sub-annual grain)
  - UCI Online Retail II  https://archive.ics.uci.edu/dataset/502/online+retail+ii
                          (order lines — the one grain below a country, and the
                           one source that is a bulk file drop rather than an API)
  - Open-Meteo ERA5       https://open-meteo.com/en/docs/historical-weather-api
                          (daily capital-city weather — the one source joined on
                           a *coordinate*, and the one with a finite budget)

Set ``INGEST_FIXTURES=1`` to read checked-in payloads instead of the live
endpoints — see `ingest/fixtures.py`. That's what CI does on pull requests.

Run:  uv run python -m ingest.pipeline
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import time
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import dlt
import duckdb
import polars as pl
import requests
from dlt.common.schema.typing import TColumnSchema

from ingest import fixtures
from modern_data_stack import db, workbook
from modern_data_stack.paths import cache_dir, warehouse_path
from modern_data_stack.ratelimit import WeightedWindowLimiter
from modern_data_stack.workbook import excel_serial_to_timestamp, extract_member

# WAREHOUSE_PATH lets a fixture run target a throwaway file instead of the real
# warehouse. It must be absolute: dbt resolves its own copy of this from `dbt/`,
# and `just test-pipeline` passes an absolute path for exactly that reason.
# Resolution — and the project root it falls back to — lives in
# `modern_data_stack.paths`, so every layer agrees on the answer without
# importing it from whichever layer happened to compute it first.
DUCKDB_PATH = warehouse_path()

OWID_CO2 = "https://raw.githubusercontent.com/owid/co2-data/master/owid-co2-data.csv"
OWID_ENERGY = "https://raw.githubusercontent.com/owid/energy-data/master/owid-energy-data.csv"

# Page size for the World Bank API (its documented maximum is 32 000, but large
# pages occasionally time out — 10 000 with pagination is the safer trade).
WB_PER_PAGE = 10_000


def wb_country_url(page: int = 1) -> str:
    """The WB /country request URL for one page (also used by the recorder)."""
    return f"https://api.worldbank.org/v2/country?format=json&per_page={WB_PER_PAGE}&page={page}"


WB_COUNTRY_API = wb_country_url()

# Eurostat: electricity prices for household consumers, medium consumption band
# DC (2 500–4 999 kWh/yr), all taxes included, in EUR/kWh. Filtered server-side to
# geo × time so the JSON-stat payload stays small.
# https://ec.europa.eu/eurostat/databrowser/view/nrg_pc_204
EU_ELEC_PRICES_API = (
    "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/nrg_pc_204"
    "?format=JSON&lang=EN&nrg_cons=KWH2500-4999&tax=I_TAX&currency=EUR&unit=KWH"
)

# World Bank WDI indicators to pull (economic + social facts, country-year grain).
# https://databank.worldbank.org/source/world-development-indicators
WB_WDI_INDICATORS = {
    "NY.GDP.PCAP.CD": "gdp_per_capita_usd",  # GDP per capita, current US$
    "NY.GDP.MKTP.CD": "gdp_usd",  # GDP, current US$
    # Constant-price GDP is what carbon intensity is divided by: current US$
    # moves with inflation and the exchange rate, which made a 21% emissions
    # cut in Japan look like a 10% intensity *rise* on a depreciating yen.
    "NY.GDP.MKTP.KD": "gdp_constant_usd",  # GDP, constant 2015 US$
    "SP.DYN.LE00.IN": "life_expectancy",  # Life expectancy at birth, years
    "SP.POP.TOTL": "population",  # Population, total
    "SI.POV.DDAY": "poverty_rate",  # Poverty headcount at $2.15/day, % of pop
    "IT.NET.USER.ZS": "internet_users_pct",  # Individuals using the internet, % pop
    "SP.URB.TOTL.IN.ZS": "urban_pop_pct",  # Urban population, % of total
    "AG.LND.FRST.ZS": "forest_area_pct",  # Forest area, % of land area
    "EG.ELC.RNEW.ZS": "renew_elec_pct",  # Renewable electricity output, % of total
    "EG.IMP.CONS.ZS": "energy_imports_pct",  # Energy imports (net), % of energy use
}

# WDI is loaded incrementally (`merge`), so its grain has to be a real key: these
# three identify a row and dlt uses them as the merge predicate. `country_code`
# (the World Bank's own 2-3 char code) and not `country_iso3`, which the API
# leaves empty on its aggregate series — five of those share `(indicator, '',
# year)`, and a merge silently keeps one of them.
WDI_PRIMARY_KEY = ("indicator", "country_code", "year")

# How far back each incremental run re-fetches. See `wdi_start_year`.
WDI_LOOKBACK_YEARS = 5

# The first year the WDI series covers, and so the first year worth asking for.
# The orchestration layer partitions `raw/wb_wdi` from here.
WDI_FIRST_YEAR = 1960

# Where the per-indicator watermarks live inside dlt's resource state.
WDI_WATERMARK_KEY = "max_year_by_indicator"

# Spelled out because this table's schema is no longer dropped and re-inferred
# every run (the point of `REFRESH` below) — an incremental resource keeps dlt's
# persisted schema, which only *widens*. `value` is the one that matters: the
# indicators are a mix of counts and ratios, and a window that happened to
# contain only integers would otherwise infer bigint and turn the first ratio
# into a `value__v_double` variant column. The key columns are non-nullable so a
# null key fails the load rather than silently escaping the merge predicate
# (`null = null` is never true, so those rows would duplicate on every run).
WDI_COLUMNS: dict[str, TColumnSchema] = {
    "indicator": {"data_type": "text", "nullable": False},
    "country_code": {"data_type": "text", "nullable": False},
    "country_iso3": {"data_type": "text"},
    "year": {"data_type": "bigint", "nullable": False},
    "value": {"data_type": "double"},
}

# Frankfurter republishes the ECB's daily euro foreign-exchange reference rates
# as JSON — no key, no quota, no auth. https://frankfurter.dev
FRANKFURTER_API = "https://api.frankfurter.dev/v1"

# Every rate is quoted *against the euro*, because that is the only thing the ECB
# publishes. It is the source's shape, not a choice: a USD-based rate is a
# division in `stg_fx_rates`, not a second request.
FX_BASE_CURRENCY = "EUR"

# The first day the reference rates exist — the euro's third trading day. Asking
# for anything earlier just returns an empty `rates` object.
FX_FIRST_DATE = "1999-01-04"

# How far back an incremental run re-asks for. Deliberately much shorter than
# WDI's five *years*, because the reason is different: the World Bank restates
# published figures as routine practice, and the ECB does not restate a fixing.
# Ten days is what closes a hole left by a run that failed after its watermark
# moved, or by a rare corrected fixing — and the merge key makes re-asking free.
FX_LOOKBACK_DAYS = 10

# One watermark for the table, not one per currency — the opposite of WDI, and
# for a reason worth stating: every currency arrives in the *same* request, so a
# newly listed one is already covered by the table-wide high-water mark. WDI
# needs the per-indicator form precisely because adding an indicator adds a
# request that has never been made before.
FX_WATERMARK_KEY = "max_rate_date"

FX_PRIMARY_KEY = ("rate_date", "quote_currency")

# Declared for the same reason as `WDI_COLUMNS`: an incremental resource keeps
# dlt's persisted schema, which only widens. `rate` is the one that matters — the
# series spans 0.85765 (GBP) to 1,725,000 (the pre-2005 Turkish lira), and a
# lookback window that happened to hold only the majors would still infer double,
# but a first load restricted to one currency would not necessarily.
FX_COLUMNS: dict[str, TColumnSchema] = {
    "rate_date": {"data_type": "date", "nullable": False},
    "base_currency": {"data_type": "text", "nullable": False},
    "quote_currency": {"data_type": "text", "nullable": False},
    "rate": {"data_type": "double"},
}


def _download(url: str, dest: Path, *, timeout: int = 300, chunk: int = 1 << 20) -> Path:
    """Stream a large file to disk, writing to a temporary name first.

    The rename is the point: an interrupted download that left a short file under
    the real name would be indistinguishable from a complete one on the next run,
    and the cache would serve a truncated workbook forever. A partial write
    leaves a `.part` behind instead, which is retried and overwritten.
    """
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, stream=True, timeout=timeout) as resp:
        resp.raise_for_status()
        with tmp.open("wb") as out:
            for block in resp.iter_content(chunk_size=chunk):
                out.write(block)
    tmp.replace(dest)
    return dest


# UCI Online Retail II — a UK online gift retailer's transactions, 2009-12 to
# 2011-12, CC BY 4.0. https://archive.ics.uci.edu/dataset/502/online+retail+ii
#
# The only source here that is a *bulk file drop* rather than an API: one 45 MB
# zip holding one workbook of two sheets, republished when the curator revises it
# and otherwise static. Everything downstream of that shape — the cache, the
# workbook reader, load-time rather than fetch-time partitions — follows from it.
RETAIL_ARCHIVE = "https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip"
RETAIL_WORKBOOK_NAME = "online_retail_II.xlsx"

# The extract's own bounds — first and last transaction month. Constants rather
# than a query because the orchestration layer needs them to *define* the
# partitions, before any data has been loaded to read them from. They are safe to
# hardcode in a way no other source's bounds would be: this is a closed archive,
# the study period ended on 2011-12-09, and a revision of the file would be a
# re-transcription of the same two years rather than an extension of them.
# `tests/test_ingest.py` checks them against the recorded fixture, so a
# re-recording that widened the window would fail rather than silently leave
# months with no partition to land in.
RETAIL_FIRST_MONTH = "2009-12"
RETAIL_LAST_MONTH = "2011-12"

# (invoice, line_number). The source has no line identifier at all, so this is
# assigned from file position — see `retail_sql` for why content can't do it and
# what that costs.
RETAIL_PRIMARY_KEY = ("invoice", "line_number")

# Rows per Arrow batch handed to dlt. Small enough that peak memory is flat over
# a full 1.07M-row load, large enough that the per-batch overhead disappears.
RETAIL_BATCH_ROWS = 100_000

# Declared rather than inferred, for the same reason `wb_wdi`'s are: this is the
# second resource whose schema isn't dropped and re-inferred each run, and a
# partition that happened to contain only whole prices would infer `bigint` for
# `unit_price` and shunt the next 1.25 into a `unit_price__v_double` variant.
# `customer_id` is text on purpose — it is an identifier that happens to look
# numeric, and the one thing nobody will ever do to it is arithmetic.
#
# No `nullable: False` on the key columns, unlike `wb_wdi` and `ecb_fx_rates` —
# though not for the reason it first looks like. Every load logs a hint-mismatch
# warning naming `invoice` and `line_number`, because Arrow fields are nullable
# by construction and dlt's schema says they aren't; removing the explicit hints
# does *not* silence it, since `primary_key` marks its own columns non-nullable
# anyway. The warning is unavoidable for an Arrow resource with a key, and it is
# harmless: dlt's hint wins and the column lands NOT NULL. They are left out
# because they would be redundant, and the assertion is worth more in dbt
# regardless — `not_null` there stores the offending rows rather than logging.
RETAIL_COLUMNS: dict[str, TColumnSchema] = {
    "invoice": {"data_type": "text"},
    "line_number": {"data_type": "bigint"},
    "stock_code": {"data_type": "text"},
    "description": {"data_type": "text"},
    "quantity": {"data_type": "bigint"},
    # `timezone: False`, and it is not cosmetic. dlt's default `timestamp` maps to
    # TIMESTAMP WITH TIME ZONE, which reads a naive value as UTC and renders it in
    # the reader's zone: a till receipt stamped 07:45 came back as
    # `2009-12-01 08:45:00+01:00` on a CET machine and would come back as 07:45 in
    # CI, so the same workbook produced a different warehouse depending on where
    # it was built. These are wall-clock shop times with no zone attached and no
    # instant to preserve — the only faithful storage is a naive timestamp.
    "invoice_ts": {"data_type": "timestamp", "timezone": False},
    "invoice_month": {"data_type": "text"},
    "unit_price": {"data_type": "double"},
    "customer_id": {"data_type": "text"},
    "country": {"data_type": "text"},
    "sheet_name": {"data_type": "text"},
}


def _csv_source(url: str) -> str:
    """The CSV to hand Polars: the live URL, or a fixture path when offline."""
    return str(fixtures.path_for(url)) if fixtures.enabled() else url


def _get_json(url: str, *, timeout: int = 120, retries: int = 3) -> dict | list:
    """GET + parse JSON with a few retries — the World Bank & Eurostat APIs
    occasionally return a transient error page or non-JSON body.

    A non-2xx status is retried and ultimately raised: without the
    `raise_for_status()` an HTML/JSON error body would parse fine and be handed
    on as if it were data.
    """
    if fixtures.enabled():
        path = fixtures.path_for(url)
        # One fixture is gzipped — the FX series is 3.6 MB of JSON whole and
        # 831 kB compressed, and it is kept whole because every discontinuity in
        # it (see `ecb_fx_rates`) is something the models are tested against.
        if path.suffix == ".gz":
            return json.loads(gzip.decompress(path.read_bytes()))
        return json.loads(path.read_text())

    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:  # ValueError = JSONDecodeError
            last = exc
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"failed to fetch JSON from {url}: {last}")


def _get_json_object(url: str, *, timeout: int = 120, retries: int = 3) -> dict:
    """`_get_json` for an endpoint that documents a JSON *object*.

    The union `_get_json` returns is honest — the World Bank really does send
    `[metadata, [records…]]` — and both World Bank callers already narrow it by
    hand, because an error object served with a 200 is a real thing those APIs
    do. This is the same check for the other branch: Eurostat's JSON-stat and
    the ECB's `{"rates": …}` are objects, and a caller that subscripts one by
    name should say so once rather than at every key.
    """
    payload = _get_json(url, timeout=timeout, retries=retries)
    if not isinstance(payload, dict):
        # `RuntimeError` rather than the `TypeError` TRY004 asks for, and
        # deliberately: nothing here passed a wrong argument — the URL was fine
        # and the *server* sent the wrong shape. That is the same fault the two
        # World Bank checks below raise `RuntimeError` for; they escape the rule
        # only because their condition is compound. Matching them is worth the
        # one suppression.
        raise RuntimeError(  # noqa: TRY004
            f"expected a JSON object from {url}, got {payload!r:.300}"
        )
    return payload


# infer_schema_length=None scans the whole file so sparse numeric columns
# (empty for the first rows) are typed as numbers, not strings.
@dlt.resource(name="owid_co2", write_disposition="replace")
def owid_co2():
    yield pl.read_csv(_csv_source(OWID_CO2), infer_schema_length=None).to_dicts()


@dlt.resource(name="owid_energy", write_disposition="replace")
def owid_energy():
    yield pl.read_csv(_csv_source(OWID_ENERGY), infer_schema_length=None).to_dicts()


def _wb_country_pages():
    """Each page of the World Bank /country endpoint, as a list of rows.

    Paginated like wb_wdi: the dimension table fits on one page today, but
    nothing guarantees it stays under WB_PER_PAGE rows forever, and a second page
    arriving with no pagination would be silently truncated.

    Split out of the resource because `weather_locations` needs the same rows for
    a different reason — the capital coordinates — and reading them through the
    same iterator is what stops the two from disagreeing about pagination or
    about what an error payload looks like.
    """
    page = 1
    while True:
        payload = _get_json(wb_country_url(page), timeout=60)
        # World Bank returns [metadata, [records...]]; anything else is an
        # error object served with a 200.
        if not (isinstance(payload, list) and len(payload) == 2):
            raise RuntimeError(f"unexpected World Bank payload for /country: {payload!r:.300}")
        meta, rows = payload
        yield rows or []
        if page >= int(meta.get("pages", 1)):
            break
        page += 1


@dlt.resource(name="wb_country", write_disposition="replace")
def wb_country():
    yield from _wb_country_pages()


def wdi_url(
    code: str,
    page: int = 1,
    start_year: int | None = None,
    end_year: int | None = None,
) -> str:
    """The WDI request URL for one indicator page (also used by the recorder).

    `start_year` adds the API's `date` range filter, which is what makes the
    incremental load cheap — see `wdi_start_year`. Without it the request is the
    whole series, 1960 onwards. `end_year` closes the window at something other
    than today, which is what a partition backfill asks for; the incremental
    path leaves it open-ended.
    """
    url = (
        f"https://api.worldbank.org/v2/country/all/indicator/{code}"
        f"?format=json&per_page={WB_PER_PAGE}&page={page}"
    )
    if start_year is not None:
        # the range needs both ends; the API tolerates a future one. UTC rather
        # than the local clock so the URL a fixture is recorded against doesn't
        # depend on which side of midnight the runner sits.
        url += f"&date={start_year}:{end_year if end_year is not None else datetime.now(UTC).year}"
    return url


def wdi_start_year(last_loaded_year: int | None) -> int | None:
    """The first year the next WDI load should ask for.

    `None` — meaning the whole series — until a load has recorded a watermark.
    After that, `WDI_LOOKBACK_YEARS` back from it rather than the watermark
    itself: the World Bank *restates* published years, so fetching only what is
    newer than the high-water mark would freeze stale revisions into the
    warehouse forever. The window is the trade-off in miniature — five years
    catches the revisions that actually move and still turns a ~190k-row pull
    into a ~15k-row one. An older restatement needs `INGEST_WDI_FULL=1`.
    """
    if last_loaded_year is None:
        return None
    return last_loaded_year - WDI_LOOKBACK_YEARS + 1


def wdi_full_reload_requested() -> bool:
    """`INGEST_WDI_FULL=1` ignores the watermark and re-fetches everything."""
    return os.environ.get("INGEST_WDI_FULL", "").lower() in {"1", "true", "yes"}


def _fetch_wdi_indicator(
    code: str, start_year: int | None = None, end_year: int | None = None
) -> list[dict]:
    """All rows for one WDI indicator, paginating until `meta.pages` is exhausted."""
    rows_out: list[dict] = []
    page = 1
    while True:
        payload = _get_json(wdi_url(code, page, start_year, end_year))
        # World Bank returns [metadata, [records...]]; anything else is an
        # error object served with a 200.
        if not (isinstance(payload, list) and len(payload) == 2):
            raise RuntimeError(f"unexpected World Bank payload for {code}: {payload!r:.300}")
        meta, rows = payload
        rows_out.extend(
            {
                "indicator": code,
                # The API's own country key, and the merge key: `countryiso3code`
                # is empty for the aggregate series ("Arab World", "World"), so
                # five of them would collide on (indicator, '', year).
                "country_code": (row.get("country") or {}).get("id"),
                "country_iso3": row.get("countryiso3code"),
                "year": int(row["date"]) if row.get("date") else None,
                "value": row.get("value"),
            }
            for row in rows or []
        )
        if page >= int(meta.get("pages", 1)):
            break
        page += 1
    return rows_out


@dlt.resource(
    name="wb_wdi",
    write_disposition="merge",
    primary_key=WDI_PRIMARY_KEY,
    columns=WDI_COLUMNS,
)
def wb_wdi(years: tuple[int, int] | None = None):
    """The one incremental resource: `merge` on `WDI_PRIMARY_KEY` with a lookback
    window, where the other four are full `replace` reloads.

    Merge is what makes a partial fetch safe — the rows the window re-fetches
    replace their previous versions instead of appending a second copy, so a run
    that asks for five years still leaves 1960 onwards intact. What it gives up
    is `replace`'s guarantee that the table is exactly what the API just served:
    a country-year the World Bank *withdraws* stays here until a full reload.

    `years` is the *backfill* path — an explicit `(first, last)` window asked for
    verbatim, for every indicator, which is what a Dagster partition key means
    here. The same merge key makes it re-runnable: loading 1995 twice leaves the
    table exactly as it was after the first time.
    """
    # Paginated: a single 20k-row page used to cover every indicator, but the
    # series grow by ~270 rows a year and were already at 87% of that cap, so
    # the next few years would have silently truncated the oldest indicators.
    #
    # The watermark is per *indicator*, not one number for the table: adding a
    # code to WB_WDI_INDICATORS then has no watermark for it and pulls the whole
    # series, where a table-wide watermark would have given the new column five
    # years of history and no error.
    watermarks = dlt.current.resource_state().setdefault(WDI_WATERMARK_KEY, {})
    full_reload = wdi_full_reload_requested()

    def window(code: str) -> tuple[int | None, int | None]:
        if years is not None:
            return years
        if full_reload:
            return (None, None)
        return (wdi_start_year(watermarks.get(code)), None)

    # Each indicator's fetch (and its pages) is independent of the others and
    # only hits the World Bank API — nothing here touches the DuckDB writer
    # lock — so a small thread pool fetches them concurrently instead of one
    # blocking call at a time.
    with ThreadPoolExecutor(max_workers=8) as pool:
        pending = {
            code: pool.submit(_fetch_wdi_indicator, code, *window(code))
            for code in WB_WDI_INDICATORS
        }
        for code, future in pending.items():
            rows = future.result()
            loaded = [row["year"] for row in rows if row["year"] is not None]
            if loaded and years is None:
                # Advanced only after a clean fetch, and committed by dlt only if
                # the load succeeds — a half-failed run can't move the watermark
                # past years that never landed.
                #
                # A backfill deliberately doesn't touch it. The watermark means
                # "everything up to here is loaded", and a partition run only
                # claims its own window: backfilling 2020-2025 into an empty
                # warehouse would otherwise leave a 2025 watermark, and the next
                # incremental run would look back five years over sixty years of
                # history that was never fetched.
                watermarks[code] = max(loaded + [watermarks.get(code, 0)])
            yield rows


@dlt.resource(name="eu_elec_prices", write_disposition="replace")
def eu_elec_prices():
    # Eurostat returns JSON-stat: a flat `value` dict keyed by the row-major
    # index over all dimensions. We filtered every dimension but geo & time to a
    # single category, so we walk geo × time and compute each flat index.
    j = _get_json_object(EU_ELEC_PRICES_API)
    dim_ids: list[str] = j["id"]
    sizes: list[int] = j["size"]
    values: dict[str, float] = j["value"]

    # Row-major strides: stride of the last dimension is 1, working leftwards.
    strides: dict[str, int] = {}
    acc = 1
    for name, size in zip(reversed(dim_ids), reversed(sizes)):
        strides[name] = acc
        acc *= size

    geo_index = j["dimension"]["geo"]["category"]["index"]
    time_index = j["dimension"]["time"]["category"]["index"]  # e.g. "2023-S1"
    for geo, gi in geo_index.items():
        for period, ti in time_index.items():
            flat = gi * strides["geo"] + ti * strides["time"]
            value = values.get(str(flat))
            if value is None:
                continue
            yield {
                "geo": geo,  # Eurostat 2-letter code (EL=Greece, UK=UK)
                "period": period,  # semi-annual, e.g. "2023-S1"
                "year": int(period[:4]),
                "price_eur_kwh": value,  # household price incl. all taxes
            }


def fx_url(start_date: str, end_date: str | None = None) -> str:
    """The Frankfurter request URL for one date range (also used by the recorder).

    The whole series is a single request — 1999 to today is 3.6 MB and answers in
    about three seconds — so there is no pagination to express here, unlike every
    other JSON source in this file.
    """
    end = end_date or datetime.now(UTC).date().isoformat()
    return f"{FRANKFURTER_API}/{start_date}..{end}?base={FX_BASE_CURRENCY}"


def fx_start_date(last_loaded_date: str | None) -> str:
    """The first date the next FX load should ask for.

    `FX_FIRST_DATE` until a load has recorded a watermark, then
    `FX_LOOKBACK_DAYS` back from it — clamped, so a watermark near the start of
    the series can't ask for dates before the euro existed.
    """
    if last_loaded_date is None:
        return FX_FIRST_DATE
    start = date.fromisoformat(last_loaded_date) - timedelta(days=FX_LOOKBACK_DAYS - 1)
    return max(start, date.fromisoformat(FX_FIRST_DATE)).isoformat()


@dlt.resource(
    name="ecb_fx_rates",
    write_disposition="merge",
    primary_key=FX_PRIMARY_KEY,
    columns=FX_COLUMNS,
)
def ecb_fx_rates():
    """The ECB's daily euro reference rates — the warehouse's first sub-annual grain.

    The payload is wide (`{"2024-01-02": {"USD": 1.0956, ...}, ...}`) and is
    unpivoted here rather than in staging, so `raw.ecb_fx_rates` lands at the
    grain the merge key is defined on. A wide landing table would need a new
    column every time the ECB lists a currency, which is exactly what dlt's
    widen-only schema handles worst.

    **The currency panel is not fixed, and that is the interesting part.** Of the
    46 codes in the series only 30 are still published. Ten stop on the last
    business day before their country adopted the euro (GRD in 2000, HRK in 2022,
    BGN in 2025); RUB stops on 2022-03-01; four more stop together in October
    2020; and ISK has a 3,341-day *interior* gap from Iceland's 2008 banking
    collapse to February 2018. Anything downstream that carries a rate forward
    has to reckon with all four shapes — see `marts.fct_fx_rates_daily`.

    Under fixtures the recorded payload is the whole series regardless of which
    window this asks for. That is harmless: the merge key means re-landing a date
    replaces it rather than duplicating it.
    """
    state = dlt.current.resource_state()
    payload = _get_json_object(fx_url(fx_start_date(state.get(FX_WATERMARK_KEY))))
    rates: dict[str, dict[str, float]] = payload.get("rates") or {}

    # ISO dates sort lexicographically, so `max` over the keys is the newest day.
    # Moved only on a non-empty response, and before the yield so it doesn't
    # depend on dlt exhausting the generator: a request whose window falls
    # entirely on a weekend legitimately returns nothing, and advancing the
    # watermark there would advance it to a date that was never loaded. dlt only
    # commits resource state if the load itself succeeds.
    if rates:
        state[FX_WATERMARK_KEY] = max(rates)

    yield [
        {
            "rate_date": day,
            "base_currency": payload.get("base", FX_BASE_CURRENCY),
            "quote_currency": currency,
            "rate": value,
        }
        for day, quotes in rates.items()
        for currency, value in quotes.items()
    ]


def _content_digest(path: Path) -> str:
    """A short content hash of a file, read in chunks rather than into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:12]


def retail_workbook() -> Path:
    """The extracted Online Retail II workbook, downloading it at most once.

    Everything else in this file is a request whose response *is* the data. This
    is a 45 MB zip that has not changed since 2023 and never will — the study
    period closed in 2011 — so re-fetching it per load would be 45 MB of
    politeness to no one, and re-fetching it per *partition* would make a
    twenty-five-month backfill a gigabyte of identical downloads. It is cached in
    `data/cache/` instead; the directory is gitignored and safe to delete.

    Under fixtures the zip is the recorded one and no download happens at all,
    which is the same code path with a different byte source — the unzip, the
    sheet discovery and the `all_varchar` read all still run.

    **A fixture run caches into its own subdirectory**, and that is not tidiness.
    The two workbooks have the same name and the same shape, so a fixture run
    writing to the shared path would leave the 30k-row slice sitting there as
    `online_retail_II.xlsx` — and the next *real* run would find it, skip the
    download and load the slice into the real warehouse with no error anywhere.
    Same failure the `_fixtures` pipeline-name suffix exists to prevent one layer
    down, and the same one an absolute `WAREHOUSE_PATH` prevents in the tests.

    **The extract is keyed on the archive's content**, which is the same failure
    one level further in. Keyed on the directory alone — "it exists, return it" —
    the cache had no way to notice that the archive underneath it had changed, so
    `just record-fixtures` rewriting the recorded zip left the *previous* slice
    sitting in `fixtures/` and every fixture test then passed against data the
    repo no longer held. A digest in the path makes a re-record a cache miss.
    Stale digest directories are left behind rather than pruned; they cost disk
    in a gitignored cache, and deleting is the one thing this function should not
    be doing on its own.
    """
    cache = Path(cache_dir()) / ("fixtures" if fixtures.enabled() else "live")
    cache.mkdir(parents=True, exist_ok=True)

    # The download moved above the cache check, because the check now needs the
    # archive to hash. It is still at most one download: the live archive is
    # itself cached, and the fixture one is in the repo.
    if fixtures.enabled():
        archive = fixtures.path_for(RETAIL_ARCHIVE)
    else:
        archive = cache / "online_retail_ii.zip"
        if not archive.exists():
            _download(RETAIL_ARCHIVE, archive)

    extracted = cache / _content_digest(archive) / RETAIL_WORKBOOK_NAME
    if extracted.exists():
        return extracted
    return extract_member(archive, extracted.parent, ".xlsx")


def retail_sql(months: tuple[str, str] | None = None) -> str:
    """The SQL that turns the workbook into `raw.retail_invoice_lines`.

    A function rather than a constant because the month filter is the partition:
    `('2010-01', '2010-03')` loads that window and nothing else. Filtering here
    rather than after the read is what makes a partition cheap — DuckDB still
    parses the whole workbook (a spreadsheet has no index), but only the selected
    rows are materialised, converted and handed to dlt.

    Three things happen in this SQL and each is deliberate:

    * **`line_number` is assigned from file position**, because the source has no
      line identifier and 34,337 rows are exact duplicates of another row — same
      invoice, product, quantity, price and timestamp. They cannot be told apart
      by content, so content cannot key them. File order can, and it is stable:
      `preserve_insertion_order` is set explicitly rather than left to DuckDB's
      default, because that default is what the key's determinism rests on and a
      future release changing it would corrupt the merge silently rather than
      loudly. `tests/test_ingest.py` reads the fixture twice and compares.
    * **Every column is cast from text exactly once**, here, where the failure is
      visible. See `modern_data_stack.workbook` for why the read is all-text.
    * **Nothing is cleaned.** `Invoice` keeps its `C` and `A` prefixes, quantities
      keep their signs, `Customer ID` keeps its 243,007 blanks and `StockCode`
      keeps both `M` and `m`. The landing table is what the publisher sent; the
      taxonomy those prefixes encode is a modelling decision and belongs in
      staging, not in a cast.
    """
    ts = excel_serial_to_timestamp('"InvoiceDate"')
    # Filtered on the underlying expression, not on the `invoice_month` alias, and
    # in WHERE rather than QUALIFY. Both matter: WHERE runs before the window, so
    # a month filter costs nothing, and it cannot renumber a kept invoice's lines
    # because it only ever removes whole invoices — a month boundary never falls
    # inside one.
    where = ""
    if months is not None:
        where = f"where strftime(invoice_ts, '%Y-%m') between '{months[0]}' and '{months[1]}'"
    return f"""
        with source as (
            select *, row_number() over () as file_row from sheets
        ),
        typed as (
            select
                "Invoice"                   as invoice,
                "StockCode"                 as stock_code,
                "Description"               as description,
                cast("Quantity" as bigint)  as quantity,
                {ts}                        as invoice_ts,
                cast("Price" as double)     as unit_price,
                "Customer ID"               as customer_id,
                "Country"                   as country,
                sheet_name,
                file_row
            from source
        ),
        filtered as (
            select *, strftime(invoice_ts, '%Y-%m') as invoice_month
            from typed
            {where}
        )
        select
            invoice,
            row_number() over (partition by invoice order by file_row) as line_number,
            stock_code,
            description,
            quantity,
            invoice_ts,
            invoice_month,
            unit_price,
            customer_id,
            country,
            sheet_name
        from filtered
    """


@dlt.resource(
    name="retail_invoice_lines",
    write_disposition="merge",
    primary_key=RETAIL_PRIMARY_KEY,
    columns=RETAIL_COLUMNS,
)
def retail_invoice_lines(months: tuple[str, str] | None = None):
    """A UK gift retailer's order lines, 2009-12 to 2011-12 — the first grain
    below a country, and the first row here that is a thing somebody *did*.

    Every other source in this file is a published statistic: an annual national
    aggregate somebody else computed. This is 1,067,371 raw events, and what it
    buys is the entire vocabulary those aggregates can't reach — a customer, an
    order, a return, a cohort, a basket, a margin.

    It is also messy in ways that are load-bearing rather than incidental, which
    is most of why it was chosen over a synthetic set. The three prefixes on
    `Invoice` are a taxonomy nobody documented (45,330 sales, 8,292 `C`
    cancellations, 6 `A` bad-debt adjustments); 3,457 negative-quantity rows sit
    on *sale* invoices and are not returns at all; and `StockCode` carries
    postage, bank charges, Amazon fees and a literal `TEST001` alongside the
    products. All of that is landed exactly as sent and sorted out in staging.

    Yielded as Arrow record batches, not dicts: a million rows through Python
    dictionaries costs about a minute and a couple of GB, and dlt writes Arrow
    straight to Parquet without ever building the row objects.

    **`to_arrow_reader`, never `.arrow()`.** `DuckDBPyRelation.arrow()` returns a
    streaming `RecordBatchReader` whose default batch is 1,000,000 rows, and a
    caller that treats it as a table gets the first batch and no warning — this
    resource silently landed exactly 1,000,000 of its 1,067,371 rows until the
    round number gave it away. The reader is the right object anyway, because
    consuming it in batches is what keeps peak memory flat, but it has to be
    *iterated*. `tests/test_ingest.py` pins the row count against the workbook's
    own so a batch-size change can't reintroduce it.
    """
    con = workbook.connect()
    con.execute(f"create or replace view sheets as {workbook.sheets_sql(retail_workbook())}")
    yield from con.sql(retail_sql(months)).to_arrow_reader(RETAIL_BATCH_ROWS)


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
# this is not a limit either — `just backfill-weather 1990 2006` goes deeper
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
# worth reading. `_get_json`'s 1.5s/3s backoff would burn all three of its
# retries in 4.5 seconds against the shortest of them.
#
# The daily window is deliberately absent: waiting out a day inside a pipeline
# run is not a retry, it is a hang. That one raises and says to come back
# tomorrow or narrow the window.
WEATHER_RETRY_AFTER_SECONDS = {"minutely": 65.0, "hourly": 660.0}
WEATHER_DEFAULT_RETRY_AFTER_SECONDS = 65.0
WEATHER_RETRIES = 6

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

    Going through `_get_json` means this is fixture-aware for free, so an offline
    run resolves the same coordinates CI recorded.

    **Fails closed.** A country in scope with no coordinates raises rather than
    being dropped: silently narrowing the request would produce a shorter
    response, and the response is matched to the request *by position*.
    """
    wanted = set(WEATHER_COUNTRIES)
    found: dict[str, tuple[float, float]] = {}
    for rows in _wb_country_pages():
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


def weather_watermark(duckdb_path: str | Path = DUCKDB_PATH) -> str | None:
    """The newest day already in `raw.om_weather_daily`, or None if there is none.

    **Read from the destination, not from dlt's resource state**, which is the
    one design decision here that everything else depends on. `wb_wdi` and
    `ecb_fx_rates` keep their watermarks in `dlt.current.resource_state()`, which
    lives in `~/.dlt` — a directory CI does not have. So on every workflow run
    dlt's state is empty and those resources re-ask for their whole series, which
    is free for them and would cost this one a fortnight of API budget.

    Carrying the table forward between releases only saves anything if the
    watermark travels *with the rows*, and the rows are the only thing a
    published DuckDB file can carry. Making the data its own watermark is also
    strictly more honest: there is no second place for it to be wrong.

    A missing file or table is "nothing loaded yet". A file that cannot be *read*
    is not — that raises, because falling back would silently re-seed from 2007.
    """
    path = Path(duckdb_path)
    if not path.exists():
        return None
    con = duckdb.connect(str(path), read_only=True)
    try:
        present = db.scalar(
            con,
            """
            select count(*) from duckdb_tables()
            where schema_name = 'raw' and table_name = 'om_weather_daily'
            """,
        )
        if not present:
            return None
        newest = db.scalar(con, "select max(weather_date) from raw.om_weather_daily")
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


def _get_weather_json(url: str, limiter: WeightedWindowLimiter, units: float) -> dict | list:
    """GET one archive window, spending `units` of a shared budget to do it.

    Under fixtures there is no budget to keep and nothing to sleep for, so this
    delegates straight to `_get_json` — a paced fixture run would otherwise make
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
    """
    if fixtures.enabled():
        return _get_json(url)

    last: Exception | None = None
    for attempt in range(WEATHER_RETRIES):
        limiter.acquire(units)
        resp = requests.get(url, timeout=300)
        limiter.charge(units)
        if resp.status_code != 429:
            resp.raise_for_status()
            return resp.json()
        last = RuntimeError(f"rate limited: {resp.text[:200]}")
        if attempt < WEATHER_RETRIES - 1:
            time.sleep(weather_retry_after(resp.text))
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
            row = {
                "country_iso3": country_iso3,
                "weather_date": day,
                "grid_latitude": entry.get("latitude"),
                "grid_longitude": entry.get("longitude"),
                "elevation_m": entry.get("elevation"),
            }
            for name, values in columns.items():
                row[name] = values[index] if index < len(values) else None
            yield row


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
        payload = _get_weather_json(
            weather_url(locations, start, end),
            limiter,
            weather_call_units(len(locations), days),
        )
        yield list(_weather_rows(payload, locations))


@dlt.source
def public_indicators(
    wdi_years: tuple[int, int] | None = None,
    retail_months: tuple[str, str] | None = None,
    weather_years: tuple[int, int] | None = None,
):
    """The eight resources as one dlt source.

    The three window arguments are threaded through to their resources rather
    than bound on afterwards, so the Dagster asset can build a source for one
    partition range with the same call the CLI makes for an unpartitioned run.
    They are separate arguments because the three are partitioned on different
    columns at different grains — years for WDI and weather, months for retail —
    and a shared one would have to be a date range that none of them takes
    directly. WDI and weather are both yearly and still keep their own argument:
    they are separate assets, and a backfill of one is not a backfill of the
    other.
    """
    return [
        owid_co2(),
        owid_energy(),
        wb_country(),
        wb_wdi(wdi_years),
        eu_elec_prices(),
        ecb_fx_rates(),
        retail_invoice_lines(retail_months),
        om_weather_daily(weather_years),
    ]


# Drop + re-infer the schema of the resources actually being loaded, so type or
# column changes at the source aren't masked by dlt's persisted (widen-only)
# schema. Unlike `drop_sources` this is safe when only part of the source runs,
# which is what Dagster does when you materialise a single raw asset.
REFRESH = "drop_resources"

# `refresh` is a property of a *run*, not of a resource, and dropping `wb_wdi`
# would take its table and its watermark with it — a full reload wearing an
# incremental costume. So the two dispositions load in two calls: replace with
# the schema-safety refresh, merge without it.
FULL_REFRESH_RESOURCES = ("owid_co2", "owid_energy", "wb_country", "eu_elec_prices")
INCREMENTAL_RESOURCES = ("wb_wdi", "ecb_fx_rates", "retail_invoice_lines", "om_weather_daily")

# Which of those the orchestration layer partitions, and it is not the same
# question as which of them merge. Three answers, and the third is the one that
# says what the rule actually is:
#
#   * `wb_wdi` — the *fetch* narrows. The API takes `&date=lo:hi` and `year` is in
#     the primary key, so a year is a re-runnable unit of work end to end.
#   * `ecb_fx_rates` — merges for the same reason WDI does and is deliberately
#     *not* partitioned. Its entire 27-year series is one three-second request, so
#     a daily partition would be 7,000 Dagster partitions standing in for it.
#   * `retail_invoice_lines` — the fetch cannot narrow at all: the source is one
#     static 45 MB workbook and there is no request to make a window out of. What
#     narrows is the *load*. Reading and converting a month is real work, the
#     cached download means twenty-five partitions are still one fetch, and
#     `invoice_month` is derived from the same timestamp the partition is keyed
#     on, so re-running one month replaces exactly that month.
#   * `om_weather_daily` — the strongest case of the four, and the first where a
#     partition is about *money* rather than time. The API takes a date range,
#     `weather_date` is in the primary key, and unlike WDI the full history
#     genuinely cannot be fetched in one run: 41 capitals over 2007-2025 is
#     ~12,200 units against a 10,000-a-day allowance. A year is the unit the
#     budget is spent in, so it had better be the unit the graph can point at.
#
# So the rule is not "the API takes a range" and never was "the disposition is
# merge" — it is whether a partition is a re-runnable unit of *work* that maps
# cleanly onto a slice of the destination. Kept here rather than in
# `orchestration/` so the covering tests in `tests/test_ingest.py` can hold both
# splits to the source without importing Dagster, an optional dependency group.
PARTITIONED_RESOURCES = ("wb_wdi", "retail_invoice_lines", "om_weather_daily")


def load_groups(resources: Iterable[str] | None = None) -> list[tuple[list[str], dict]]:
    """The resource groups to load, in order, each with the `run()` kwargs it needs.

    `resources` restricts the result to a subset — Dagster passes whichever raw
    assets were selected. Empty groups are dropped, so materialising `raw/wb_wdi`
    alone doesn't try to run a load with no resources in it.
    """
    wanted = None if resources is None else set(resources)
    groups = []
    for names, kwargs in (
        (FULL_REFRESH_RESOURCES, {"refresh": REFRESH}),
        (INCREMENTAL_RESOURCES, {}),
    ):
        selected = [name for name in names if wanted is None or name in wanted]
        if selected:
            groups.append((selected, kwargs))
    return groups


def build_pipeline() -> dlt.Pipeline:
    """The one dlt pipeline definition, shared by the CLI and the Dagster assets.

    A fixture run gets its own pipeline name, which is what keeps its *state*
    out of the real pipeline's. dlt keeps state in its own pipelines directory,
    keyed on the pipeline name alone — not on the destination — so a fixture run
    leaving a WDI watermark behind would hand it to the next real run, which
    would then fetch a five-year window on the assumption that history it never
    loaded is already there.

    That directory is `~/.dlt/pipelines/<name>/` **if `~/.dlt` already exists**,
    and `$XDG_DATA_HOME/dlt/pipelines/<name>/` otherwise — dlt prefers the legacy
    location when it finds one and says so in a UserWarning. Both paths can exist
    on one machine with only the first live, so read the warning rather than the
    directory listing: `just dlt-state` asks dlt instead of guessing.
    """
    # **Arrow data does not get `_dlt_load_id` unless you ask for it.** dlt adds
    # that column when it normalises row objects, but the Arrow/Parquet path
    # writes the table through untouched and the load-id normaliser is off by
    # default — so `raw.retail_invoice_lines` landed without the one column every
    # other table here has, and lost it silently: nothing errors, the load
    # succeeds, and the table simply has no provenance.
    #
    # It costs three things, none of them loud. `dbt source freshness` reads
    # exactly this column, so the source can never be checked. `pipeline_sources`
    # skips any table lacking it, so the observability page under-reports by one
    # row and looks complete. And `source_loaded_at` — the "which extract did
    # this number come from" column that `dim_grid_emission_factors` exists to
    # carry — has nothing to read.
    #
    # Set here rather than in an env var or `.dlt/config.toml` so it travels with
    # the pipeline definition: the Dagster asset, the CLI and the tests all build
    # their pipeline through this function, and a config file would be a fourth
    # place to remember.
    os.environ.setdefault("NORMALIZE__PARQUET_NORMALIZER__ADD_DLT_LOAD_ID", "true")

    suffix = "_fixtures" if fixtures.enabled() else ""
    return dlt.pipeline(
        pipeline_name=f"modern_data_stack{suffix}",
        destination=dlt.destinations.duckdb(DUCKDB_PATH),
        dataset_name="raw",
    )


def main() -> None:
    pipeline = build_pipeline()
    for names, kwargs in load_groups():
        print(pipeline.run(public_indicators().with_resources(*names), **kwargs))


if __name__ == "__main__":
    main()
