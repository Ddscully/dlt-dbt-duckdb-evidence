"""Record the ingest fixtures that CI runs against.

Hits the seven live endpoints once, trims each payload to a representative slice,
and writes `tests/fixtures/ingest/`. Everything downstream of dlt then has real
data to chew on without a pull request depending on OWID being up.

Run:  uv run python -m scripts.record_fixtures

Re-record when a source changes shape (new column, renamed field, a WDI
indicator added to `WB_WDI_INDICATORS`) — the nightly live run is what tells you
that happened. Commit the result; the fixtures are checked in on purpose.

## What gets trimmed, and what deliberately doesn't

Row filters only, never column filters: dropping columns would hide a renamed
field from `stg_*` and make the fixtures agree with a model that no longer
matches the source. `COUNTRIES` is the only knob.

The World Bank country dimension and the Eurostat payload are kept whole — both
are small, `wb_country` is the dimension the overrides seed is diffed against,
and the Eurostat JSON-stat grid can't be subset without rebuilding its index. The
ECB rates are kept whole for a third reason — see `record_fx`.
"""

from __future__ import annotations

import gzip
import io
import json
import tempfile
import zipfile
from datetime import date
from pathlib import Path

import polars as pl

from ingest.fixtures import FIXTURE_DIR, path_for
from ingest.pipeline import (
    EU_ELEC_PRICES_API,
    FX_FIRST_DATE,
    OWID_CO2,
    OWID_ENERGY,
    RETAIL_ARCHIVE,
    RETAIL_WORKBOOK_NAME,
    WB_COUNTRY_API,
    WB_WDI_INDICATORS,
    WEATHER_RATE_LIMITS,
    _get_json,
    _get_weather_json,
    fx_url,
    retail_sql,
    retail_workbook,
    wdi_url,
    weather_call_units,
    weather_locations,
    weather_url,
)
from modern_data_stack import workbook
from modern_data_stack.db import scalar
from modern_data_stack.ratelimit import WeightedWindowLimiter

# A slice wide enough that the modelled layers have something to do:
#   - every World Bank region and all four income groups;
#   - TWN, which the World Bank omits, so the `country_overrides` seed and the
#     union in `stg_country` are actually exercised;
#   - GRC and GBR, whose Eurostat geo codes (EL, UK) are the two the
#     `stg_eu_electricity_prices` remap exists for.
COUNTRIES = [
    "USA",  # North America, high income
    "CHN",  # East Asia & Pacific, upper middle
    "IND",  # South Asia, lower middle
    "JPN",
    "DEU",
    "FRA",
    "SWE",
    "POL",  # Europe & Central Asia, high income
    "GBR",  # ... and Eurostat's `UK` geo code
    "GRC",  # ... and Eurostat's `EL`
    "EGY",  # Middle East & North Africa, lower middle
    "BRA",  # Latin America & Caribbean, upper middle
    "NGA",
    "KEN",
    "ZAF",  # Sub-Saharan Africa
    "ETH",  # ... and the only low-income group, which co2_intensity ranks within
    "TWN",  # absent from the World Bank entirely — the overrides seed's reason to exist
]

# WDI before 1990 is mostly nulls for these indicators and tripled the fixture
# size for nothing. The OWID CSVs keep their full history — they gzip well, and
# the emissions series is interesting back to the 19th century.
WDI_MIN_YEAR = 1990


def _write(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(path, mode) as fh:
        fh.write(data)
    print(f"  {path.name:<28} {path.stat().st_size:>9,} bytes")


def record_owid(url: str) -> None:
    """Filter an OWID CSV to COUNTRIES and re-emit it gzipped.

    Written back as CSV, not Parquet, so the fixture still goes through
    `pl.read_csv(..., infer_schema_length=None)` — the inference behaviour the
    pipeline depends on is part of what CI should be testing.
    """
    df = pl.read_csv(url, infer_schema_length=None)
    kept = df.filter(pl.col("iso_code").is_in(COUNTRIES))
    _write(path_for(url), gzip.compress(kept.write_csv().encode()))


def record_wb_country() -> None:
    """The full country dimension — ~300 rows, and the overrides seed is
    defined as 'the ISO3 codes this endpoint doesn't return'."""
    _write(path_for(WB_COUNTRY_API), json.dumps(_get_json(WB_COUNTRY_API, timeout=60)))


def record_wdi() -> None:
    """One file per indicator, filtered to COUNTRIES and recent years.

    Written as a single page. Pagination is covered by `tests/test_ingest.py`
    with a mocked payload; making the fixtures multi-page would only test that
    the recorder can count.
    """
    for code in WB_WDI_INDICATORS:
        rows: list[dict] = []
        page = 1
        while True:
            payload = _get_json(wdi_url(code, page))
            # World Bank returns [metadata, [records...]]; anything else is an
            # error object served with a 200 (e.g. a retired indicator code).
            if not (isinstance(payload, list) and len(payload) == 2):
                raise RuntimeError(f"unexpected World Bank payload for {code}: {payload!r:.300}")
            meta, page_rows = payload
            rows += [
                row
                for row in page_rows or []
                if row.get("countryiso3code") in COUNTRIES
                and row.get("date")
                and int(row["date"]) >= WDI_MIN_YEAR
            ]
            if page >= int(meta.get("pages", 1)):
                break
            page += 1
        if not rows:
            raise RuntimeError(f"{code} returned no rows for the fixture countries")
        header = {"page": 1, "pages": 1, "per_page": len(rows), "total": len(rows)}
        _write(path_for(wdi_url(code)), json.dumps([header, rows]))


def record_eurostat() -> None:
    """The whole JSON-stat cube. It's already filtered server-side to one
    category per dimension bar geo × time, and subsetting geo would mean
    rebuilding the dimension index the pipeline walks."""
    _write(path_for(EU_ELEC_PRICES_API), json.dumps(_get_json(EU_ELEC_PRICES_API)))


def record_fx() -> None:
    """The whole ECB reference-rate series, gzipped.

    Not trimmed at all, which is the exception `COUNTRIES` doesn't cover: there
    is no country in this payload, and the interesting structure is *when each
    currency starts and stops*. Cutting the date range would throw away the euro
    changeovers, the 2022 rouble suspension and Iceland's nine-year gap — which
    are the four shapes `fct_fx_rates_daily` exists to handle, and so the four
    things CI should be exercising. 3.6 MB whole, 831 kB compressed.
    """
    url = fx_url(FX_FIRST_DATE)
    payload = json.dumps(_get_json(url))
    _write(path_for(url), gzip.compress(payload.encode()))


def record_weather() -> None:
    """Three calendar years of daily weather for all 41 capitals, gzipped.

    Two departures from `COUNTRIES`, and both are forced rather than chosen:

    * **Every location, never a subset.** Open-Meteo matches a multi-location
      response to its request *by position* — the entries carry a `location_id`
      except the first, which has none — so a fixture recorded for a subset would
      be read back against the full 41 and hand each country its neighbour's
      weather. There is no key to repair that with.
    * **A short date range instead**, which is the trim `COUNTRIES` would
      normally be. 2020-2022 is chosen for what it contains: a leap day, two
      calendar-year boundaries, and the 2021/2022 pair the heating-degree-day
      comparison is built on, so CI computes the same numbers the analysis does.

    ~1,900 units of Open-Meteo's budget, which is the one recorder call here with
    a price attached — see `weather_call_units`. Recording is therefore not free
    to repeat, and a re-record that fails partway costs the budget anyway.

    **Fetched through the paced path, not `_get_json`.** That is not defensive
    tidying: this call is the single largest weather request the repo ever makes,
    and it is typically made right after someone has been exploring the API by
    hand, so it is the *most* likely of all of them to meet a 429. Going through
    `_get_json` — as this did in its first draft — gives it three retries over
    4.5 seconds against a limit that wants a minute or an hour, and the recorder
    then fails having spent the budget it needed.
    """
    locations = weather_locations()
    url = weather_url(locations, WEATHER_FIXTURE_FIRST_DAY, WEATHER_FIXTURE_LAST_DAY)
    days = (
        date.fromisoformat(WEATHER_FIXTURE_LAST_DAY) - date.fromisoformat(WEATHER_FIXTURE_FIRST_DAY)
    ).days + 1
    payload = json.dumps(
        _get_weather_json(
            url,
            WeightedWindowLimiter(WEATHER_RATE_LIMITS),
            weather_call_units(len(locations), days),
        )
    )
    _write(path_for(url), gzip.compress(payload.encode()))
    print(f"  {'':<28} {len(locations):>9,} locations, {days:,} days")


# The recorded window. Not a lookback off today: a fixture whose contents moved
# with the recording date would make every downstream assertion about it — the
# degree-day figures in the course material, the row counts in the tests — true
# only until the next re-record.
WEATHER_FIXTURE_FIRST_DAY = "2020-01-01"
WEATHER_FIXTURE_LAST_DAY = "2022-12-31"


def record_retail() -> None:
    """A slice of the Online Retail II workbook, as a zipped workbook.

    `COUNTRIES` is no help here — this is one UK retailer, so the trim is by
    *invoice*, and whole invoices only. Half an invoice would break every
    invoice-level test for a reason that exists nowhere but the fixture.

    **The slice is defined by the shapes the models have to handle, not by a
    row count**, because this source's value is its mess. Sampling invoices at
    random would keep the volume and lose the point: 6 bad-debt adjustments and
    a single positive line on a cancellation invoice do not survive a 3% sample,
    and those are exactly the rows the staging taxonomy exists for. So each
    shape is selected explicitly and capped, and `tests/test_fixtures.py`
    asserts the recorded file still holds all of them.

    Two departures worth naming:

    * **One sheet, named for the span it covers**, where the real workbook has
      two. DuckDB's xlsx writer replaces a file per `COPY`, so a two-sheet
      fixture would have to be assembled at the zip level by hand. The union-by-
      name path that would exercise is covered directly and more sharply by
      `tests/test_workbook.py`, which builds a two-sheet book with its columns
      deliberately out of order — a thing the real source doesn't even do.
    * **It is the largest fixture in the repo** (~1.5 MB against the FX series'
      843 kB) and it does not compress further, because an `.xlsx` is already a
      deflated zip and the outer zip is pure container. That is the price of the
      container being part of what CI tests.
    """
    con = workbook.connect()
    con.execute(f"create or replace view sheets as {workbook.sheets_sql(retail_workbook())}")
    con.execute(f"create or replace view typed as {retail_sql()}")
    con.execute(RETAIL_FIXTURE_SELECTION)

    with tempfile.TemporaryDirectory() as tmp:
        book = Path(tmp) / RETAIL_WORKBOOK_NAME
        con.execute(
            f"""copy (
                    select "Invoice", "StockCode", "Description", "Quantity",
                           "InvoiceDate", "Price", "Customer ID", "Country"
                    from sheets
                    where "Invoice" in (select invoice from kept_invoices)
                )
                to '{book}' (format xlsx, header true, sheet '{RETAIL_FIXTURE_SHEET}')"""
        )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(book, RETAIL_WORKBOOK_NAME)
        _write(path_for(RETAIL_ARCHIVE), buffer.getvalue())

    kept = scalar(
        con,
        'select count(*) from sheets where "Invoice" in (select invoice from kept_invoices)',
    )
    total = scalar(con, "select count(*) from sheets")
    print(f"  {'':<28} {kept:>9,} of {total:,} rows ({100 * kept / total:.1f}%)")


# One CTE per shape the staging taxonomy has to cope with, each capped. Order
# matters only in that `monthly_topup` runs last and guarantees all 25 months are
# present however the others fell — a month missing from the fixture would be a
# Dagster partition that legitimately loads nothing, which is indistinguishable
# from one that failed.
RETAIL_FIXTURE_SELECTION = """
create or replace table kept_invoices as
with special_codes as (          -- POST, DOT, M, D, S, BANK CHARGES, AMAZONFEE, TEST001…
    select distinct invoice from typed
    where not regexp_matches(stock_code, '^[0-9]{5}')
    qualify row_number() over (partition by upper(stock_code) order by invoice) <= 3
),
write_offs as (                  -- negative quantity on a *sale* invoice: not a return
    select distinct invoice from typed
    where quantity < 0 and not starts_with(invoice, 'C')
    qualify row_number() over (order by invoice) <= 80
),
adjustments as (                 -- all six of them; a sample would keep none
    select distinct invoice from typed where starts_with(invoice, 'A')
),
positive_cancellation as (       -- the single line that breaks "C implies negative"
    select distinct invoice from typed where starts_with(invoice, 'C') and quantity > 0
),
regulars as (                    -- customers with enough history for cohorts and RFM
    select customer_id from typed
    where customer_id is not null
    group by 1
    having count(distinct invoice) >= 4 and count(distinct invoice_month) >= 3
    qualify row_number() over (order by md5(customer_id)) <= 70
),
regular_invoices as (
    select distinct invoice from typed
    where customer_id in (select customer_id from regulars)
),
anonymous as (                   -- the 22.8% with no customer id are real revenue
    select distinct invoice from typed where customer_id is null
    qualify row_number() over (order by md5(invoice)) <= 40
),
monthly_topup as (
    select invoice from (select distinct invoice, invoice_month from typed)
    qualify row_number() over (partition by invoice_month order by md5(invoice)) <= 8
)
select invoice from special_codes
union select invoice from write_offs
union select invoice from adjustments
union select invoice from positive_cancellation
union select invoice from regular_invoices
union select invoice from anonymous
union select invoice from monthly_topup
"""

RETAIL_FIXTURE_SHEET = "Year 2009-2011"


def main() -> None:
    print(f"recording fixtures for {len(COUNTRIES)} countries into {FIXTURE_DIR}")
    record_owid(OWID_CO2)
    record_owid(OWID_ENERGY)
    record_wb_country()
    record_wdi()
    record_eurostat()
    record_fx()
    record_retail()
    record_weather()
    print("done — commit tests/fixtures/ingest/")


if __name__ == "__main__":
    main()
