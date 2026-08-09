"""dlt ingestion: pull public CSV/JSON sources into the DuckDB warehouse.

Sources (all freely licensed; country + year keyed apart from the last):
  - OWID CO2 & GHG        https://github.com/owid/co2-data
  - OWID Energy           https://github.com/owid/energy-data
  - World Bank WDI        https://databank.worldbank.org/source/world-development-indicators
  - World Bank countries  https://api.worldbank.org/v2/country?format=json  (dimension table)
  - Eurostat prices       https://ec.europa.eu/eurostat/databrowser/view/nrg_pc_204  (EU only)
  - ECB reference rates   https://frankfurter.dev  (daily FX — the one sub-annual grain)

Set ``INGEST_FIXTURES=1`` to read checked-in payloads instead of the live
endpoints — see `ingest/fixtures.py`. That's what CI does on pull requests.

Run:  uv run python -m ingest.pipeline
"""

from __future__ import annotations

import gzip
import json
import os
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

import dlt
import polars as pl
import requests

from ingest import fixtures
from modern_data_stack.paths import warehouse_path

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
WDI_COLUMNS = {
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
FX_COLUMNS = {
    "rate_date": {"data_type": "date", "nullable": False},
    "base_currency": {"data_type": "text", "nullable": False},
    "quote_currency": {"data_type": "text", "nullable": False},
    "rate": {"data_type": "double"},
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
    j = _get_json(EU_ELEC_PRICES_API)
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
    payload = _get_json(fx_url(fx_start_date(state.get(FX_WATERMARK_KEY))))
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


@dlt.source
def public_indicators(wdi_years: tuple[int, int] | None = None):
    """The six resources as one dlt source.

    `wdi_years` is threaded through to `wb_wdi` rather than bound onto the
    resource afterwards, so the Dagster asset can build a source for one
    partition range with the same call the CLI makes for an incremental run.
    """
    return [
        owid_co2(),
        owid_energy(),
        wb_country(),
        wb_wdi(wdi_years),
        eu_elec_prices(),
        ecb_fx_rates(),
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
INCREMENTAL_RESOURCES = ("wb_wdi", "ecb_fx_rates")

# Which of those the orchestration layer partitions, and it is not the same
# question as which of them merge. A partition has to be a window the API will
# actually serve: WDI takes `&date=lo:hi` and its primary key contains `year`, so
# a year is a re-runnable unit of work. `ecb_fx_rates` merges for the same reason
# WDI does and is *not* partitioned — a daily partition over 7,000 days would be
# 7,000 Dagster partitions standing in for a single three-second request. Kept
# here rather than in `orchestration/` so the covering test in
# `tests/test_ingest.py` can hold it to the source without importing Dagster,
# which is an optional dependency group.
PARTITIONED_RESOURCES = ("wb_wdi",)


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
