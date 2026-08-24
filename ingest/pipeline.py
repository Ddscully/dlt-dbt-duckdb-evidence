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
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import dlt
import polars as pl
import requests
from dlt.common.schema.typing import TColumnSchema

from ingest import fixtures
from modern_data_stack import workbook
from modern_data_stack.paths import cache_dir, warehouse_path
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


@dlt.resource(name="wb_country", write_disposition="replace")
def wb_country():
    # Paginated like wb_wdi: the dimension table fits on one page today, but
    # nothing guarantees it stays under WB_PER_PAGE rows forever, and a second
    # page arriving with no pagination would be silently truncated.
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


@dlt.source
def public_indicators(
    wdi_years: tuple[int, int] | None = None,
    retail_months: tuple[str, str] | None = None,
):
    """The seven resources as one dlt source.

    The two window arguments are threaded through to their resources rather than
    bound on afterwards, so the Dagster asset can build a source for one
    partition range with the same call the CLI makes for an unpartitioned run.
    They are separate arguments because the two are partitioned on different
    columns at different grains — years for WDI, months for retail — and a shared
    one would have to be a date range that neither takes directly.
    """
    return [
        owid_co2(),
        owid_energy(),
        wb_country(),
        wb_wdi(wdi_years),
        eu_elec_prices(),
        ecb_fx_rates(),
        retail_invoice_lines(retail_months),
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
INCREMENTAL_RESOURCES = ("wb_wdi", "ecb_fx_rates", "retail_invoice_lines")

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
#
# So the rule is not "the API takes a range" and never was "the disposition is
# merge" — it is whether a partition is a re-runnable unit of *work* that maps
# cleanly onto a slice of the destination. Kept here rather than in
# `orchestration/` so the covering tests in `tests/test_ingest.py` can hold both
# splits to the source without importing Dagster, an optional dependency group.
PARTITIONED_RESOURCES = ("wb_wdi", "retail_invoice_lines")


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
    out of the real pipeline's. dlt keeps state in its own pipelines directory
    (`~/.local/share/dlt/pipelines/<name>/`) keyed on the pipeline name alone —
    not on the destination — so a fixture run leaving
    a WDI watermark behind would hand it to the next real run, which would then
    fetch a five-year window on the assumption that history it never loaded is
    already there.
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
