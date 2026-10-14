"""The World Bank: the country dimension and the WDI indicator panel.

Two resources with opposite dispositions: `wb_country` replaces a small
dimension; `wb_wdi` merges a five-year lookback window over a per-indicator
watermark.

`country_pages` is public because `ingest.sources.weather` reads it for capital
coordinates.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import dlt
from dlt.common.schema.typing import TColumnSchema

from ingest import http
from modern_data_stack import env

# Logged, not printed: Dagster captures a step's stdout (`ingest.sources.weather`).
log = logging.getLogger(__name__)

# Below the documented maximum of 32,000, whose pages occasionally time out.
WB_PER_PAGE = 10_000

# The page size a stale WDI series is asked for again. Any other value will do:
# it only has to make a URL neither cache in front of the API has seen. See
# `_fetch_wdi_indicator`.
WB_STALE_RETRY_PER_PAGE = 5_000


def wb_country_url(page: int = 1) -> str:
    """The WB /country request URL for one page (also used by the recorder)."""
    return f"https://api.worldbank.org/v2/country?format=json&per_page={WB_PER_PAGE}&page={page}"


WB_COUNTRY_API = wb_country_url()

# World Bank WDI indicators to pull (economic + social facts, country-year grain).
# https://databank.worldbank.org/source/world-development-indicators
WB_WDI_INDICATORS = {
    "NY.GDP.PCAP.CD": "gdp_per_capita_usd",  # GDP per capita, current US$
    "NY.GDP.MKTP.CD": "gdp_usd",  # GDP, current US$
    # The carbon-intensity denominator: current US$ moves with inflation and the
    # exchange rate, so dividing by it can turn an emissions cut into a rise.
    "NY.GDP.MKTP.KD": "gdp_constant_usd",  # GDP, constant 2015 US$
    "SP.DYN.LE00.IN": "life_expectancy",  # Life expectancy at birth, years
    "SP.POP.TOTL": "population",  # Population, total
    "SI.POV.DDAY": "poverty_rate",  # Poverty at $3.00/day, 2021 PPP, % of pop
    "IT.NET.USER.ZS": "internet_users_pct",  # Individuals using the internet, % pop
    "SP.URB.TOTL.IN.ZS": "urban_pop_pct",  # Urban population, % of total
    "AG.LND.FRST.ZS": "forest_area_pct",  # Forest area, % of land area
    "EG.ELC.RNEW.ZS": "renew_elec_pct",  # Renewable electricity output, % of total
    "EG.IMP.CONS.ZS": "energy_imports_pct",  # Energy imports (net), % of energy use
}

# The merge key. `country_code` (the World Bank's own code), not `country_iso3`,
# which is empty on the aggregate series, so they would all share
# `(indicator, '', year)` and the merge would keep one.
WDI_PRIMARY_KEY = ("indicator", "country_code", "year")

# How far back each incremental run re-fetches. See `wdi_start_year`.
WDI_LOOKBACK_YEARS = 5

# The first year the WDI series covers, and the floor of a backfill's range.
WDI_FIRST_YEAR = 1960

# Where the per-indicator watermarks live inside dlt's resource state.
WDI_WATERMARK_KEY = "max_year_by_indicator"

# Declared: a merge keeps dlt's widen-only schema, so integer-valued indicators
# would infer bigint and send the first ratio into a `value__v_double` column.
# Keys are non-nullable, because `null = null` never matches the merge predicate.
WDI_COLUMNS: dict[str, TColumnSchema] = {
    "indicator": {"data_type": "text", "nullable": False},
    "country_code": {"data_type": "text", "nullable": False},
    "country_iso3": {"data_type": "text"},
    "year": {"data_type": "bigint", "nullable": False},
    "value": {"data_type": "double"},
}


def country_pages():
    """Each page of the World Bank /country endpoint, as a list of rows.

    Paginated although the table fits on one page today: a second page would
    otherwise be silently dropped. Shared with `weather_locations`, so the two
    agree on pagination and on what an error payload looks like.
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
    *,
    per_page: int = WB_PER_PAGE,
) -> str:
    """The WDI request URL for one indicator page (also used by the recorder).

    `start_year` adds the API's `date` range filter, which is what makes the
    incremental load cheap — see `wdi_start_year`. Without it the request is the
    whole series, 1960 onwards. `end_year` closes the window at something other
    than today, which is what a year-range backfill (`just backfill-wdi`) asks
    for; the incremental path leaves it open-ended. `per_page` changes only for a
    stale series.
    """
    url = (
        f"https://api.worldbank.org/v2/country/all/indicator/{code}"
        f"?format=json&per_page={per_page}&page={page}"
    )
    if start_year is not None:
        # Both ends, the API tolerating a future one; UTC, so a recorded fixture's
        # URL does not depend on the runner's side of midnight.
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
    """`INGEST_WDI_FULL=1` drops the landing table and re-fetches every series.

    `ingest.pipeline.load_groups` moves `wb_wdi` into the refreshed group, which
    drops the table and its watermarks; the resource then ignores the (now empty)
    watermark and asks for each series whole. A merge only adds and updates, so
    a country-year the World Bank withdraws stays in a local tree until this
    runs; the workflows never hold one, because they build from empty.
    """
    return env.flag("INGEST_WDI_FULL")


def _edition(meta: dict) -> tuple:
    """What identifies the edition a page was cut from: two pages of one series
    that disagree on any of these came from different copies."""
    return (meta.get("lastupdated"), meta.get("sourceid"), meta.get("total"), meta.get("pages"))


def _mixed_editions(metas: list[dict]) -> bool:
    """Pages of one request that came from more than one edition.

    Each page is its own URL, cached on its own, so a fresh page 1 can sit in
    front of a stale page 2; judging the rows together, the codes on page 1
    would hide the empty ones on page 2.
    """
    return len({_edition(meta) for meta in metas}) > 1


def _editions(metas: list[dict]) -> str:
    """The distinct editions among `metas`, in page order, for a log line or an error."""
    seen = list(dict.fromkeys(_edition(meta) for meta in metas))
    return " vs ".join(
        f"lastupdated {e[0]}, sourceid {e[1]}, total {e[2]}, pages {e[3]}" for e in seen
    )


def _is_stale(rows: list[dict]) -> bool:
    """Rows, and not one with a three-letter `countryiso3code`: a stale copy.

    What the World Bank has served for a single URL, for up to two days: an old
    edition of the series with every code empty. A current series has the code
    on all but a few aggregates; an empty series (a bad indicator code) has no
    rows, and is `wdi_indicators_all_present`'s to report.
    """
    return bool(rows) and not any(len(row["country_iso3"] or "") == 3 for row in rows)


def _fetch_wdi_indicator(
    code: str, start_year: int | None = None, end_year: int | None = None
) -> list[dict]:
    """All rows for one WDI indicator, asked for again once if the copy is stale
    or its pages come from different editions.

    The stale copy is cached per URL, twice: the origin keeps it for a day, and
    each Cloudflare edge for a day from its own fetch. The origin ignores the
    order of query parameters, so the retry changes a value, `per_page`, which
    is a URL new to both. Every page is a URL of its own, so the pages of one
    request can mix editions; the retry changes every page's URL at once. A
    second failure raises, naming the edition or editions, rather than landing
    rows that `stg_wdi` would drop.
    """
    rows, metas = _fetch_wdi_pages(code, start_year, end_year, WB_PER_PAGE)
    if _mixed_editions(metas):
        reason = f"pages from different editions ({_editions(metas)})"
    elif _is_stale(rows):
        reason = f"a stale copy (lastupdated {metas[0].get('lastupdated')})"
    else:
        return rows
    log.warning(
        "wb_wdi: %s served %s; asking again with per_page=%d",
        code, reason, WB_STALE_RETRY_PER_PAGE,
    )  # fmt: skip
    rows, metas = _fetch_wdi_pages(code, start_year, end_year, WB_STALE_RETRY_PER_PAGE)
    if _mixed_editions(metas):
        raise RuntimeError(
            f"World Bank served pages of {code} from different editions at two page "
            f"sizes ({_editions(metas)}): {wdi_url(code, 1, start_year, end_year)}"
        )
    if _is_stale(rows):
        raise RuntimeError(
            f"World Bank served a stale copy of {code} at two page sizes "
            f"(lastupdated {metas[0].get('lastupdated')}, {len(rows)} rows, "
            f"no countryiso3code): {wdi_url(code, 1, start_year, end_year)}"
        )
    return rows


def _fetch_wdi_pages(
    code: str, start_year: int | None, end_year: int | None, per_page: int
) -> tuple[list[dict], list[dict]]:
    """Every page of one request, until `meta.pages` is exhausted, and every
    page's `meta`, so the caller can tell whether they agree on the edition."""
    rows_out: list[dict] = []
    metas: list[dict] = []
    page = 1
    while True:
        payload = http.get_json(wdi_url(code, page, start_year, end_year, per_page=per_page))
        # World Bank returns [metadata, [records...]]; anything else is an
        # error object served with a 200.
        if not (isinstance(payload, list) and len(payload) == 2):
            raise RuntimeError(f"unexpected World Bank payload for {code}: {payload!r:.300}")
        meta, rows = payload
        metas.append(meta)
        rows_out.extend(
            {
                "indicator": code,
                # The merge key: see `WDI_PRIMARY_KEY`.
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
    return rows_out, metas


@dlt.resource(
    name="wb_wdi",
    write_disposition="merge",
    primary_key=WDI_PRIMARY_KEY,
    columns=WDI_COLUMNS,
)
def wb_wdi(years: tuple[int, int] | None = None):
    """WDI indicators, merged on `WDI_PRIMARY_KEY` over a lookback window.

    Merge makes a partial fetch safe: re-fetched rows replace their previous
    versions, so a five-year window leaves 1960 onwards intact. The cost is that
    a country-year the World Bank withdraws stays until `just ingest-wdi-full`,
    which drops the table and re-fetches it (the workflows never hold one: they
    build from empty).

    `years` is the backfill path — an explicit `(first, last)` window for every
    indicator, which the Dagster asset takes as `YearRange` run config rather
    than as partitions (docs/decisions/0002). The merge key makes it idempotent.
    """
    # Per-indicator watermarks: a code newly added to WB_WDI_INDICATORS has none
    # and pulls its whole series, where a table-wide one would give it five years.
    watermarks = dlt.current.resource_state().setdefault(WDI_WATERMARK_KEY, {})
    full_reload = wdi_full_reload_requested()

    def window(code: str) -> tuple[int | None, int | None]:
        if years is not None:
            return years
        if full_reload:
            return (None, None)
        return (wdi_start_year(watermarks.get(code)), None)

    # Indicators are independent HTTP fetches that never touch the writer lock,
    # so they run concurrently.
    with ThreadPoolExecutor(max_workers=8) as pool:
        pending = {
            code: pool.submit(_fetch_wdi_indicator, code, *window(code))
            for code in WB_WDI_INDICATORS
        }
        for code, future in pending.items():
            rows = future.result()
            # The latest *observed* year: the World Bank serves a row for a year
            # it has not published yet, with a null value.
            loaded = [
                row["year"] for row in rows if row["year"] is not None and row["value"] is not None
            ]
            if loaded and years is None:
                # Committed only if the load succeeds. Clamped to this year: the
                # World Bank serves projections, and a 2050 watermark asks for a
                # backwards range the API ignores, making every run a full fetch.
                # A backfill leaves it alone, covering only its own window.
                watermarks[code] = min(
                    max(loaded + [watermarks.get(code, 0)]), datetime.now(UTC).year
                )
            yield rows
