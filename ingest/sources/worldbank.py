"""The World Bank: the country dimension and the WDI indicator panel.

Two resources with opposite dispositions. `wb_country` replaces a small
dimension; `wb_wdi` merges a five-year lookback window over a per-indicator
watermark, and is the one resource whose column types are declared rather than
inferred.

`country_pages` is public because `ingest.sources.weather` reads it for capital
coordinates — a cross-source dependency that was invisible while everything
shared one namespace.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import dlt
from dlt.common.schema.typing import TColumnSchema

from ingest import http

# Page size for the World Bank API (its documented maximum is 32 000, but large
# pages occasionally time out — 10 000 with pagination is the safer trade).
WB_PER_PAGE = 10_000


def wb_country_url(page: int = 1) -> str:
    """The WB /country request URL for one page (also used by the recorder)."""
    return f"https://api.worldbank.org/v2/country?format=json&per_page={WB_PER_PAGE}&page={page}"


WB_COUNTRY_API = wb_country_url()

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


def country_pages():
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
        payload = http.get_json(wb_country_url(page), timeout=60)
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
    yield from country_pages()


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
        payload = http.get_json(wdi_url(code, page, start_year, end_year))
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
