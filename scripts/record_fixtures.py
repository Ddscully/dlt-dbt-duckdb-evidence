"""Record the ingest fixtures that CI runs against.

Hits the five live endpoints once, trims each payload to a representative slice,
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
and the Eurostat JSON-stat grid can't be subset without rebuilding its index.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import polars as pl

from ingest.fixtures import FIXTURE_DIR, path_for
from ingest.pipeline import (
    EU_ELEC_PRICES_API,
    OWID_CO2,
    OWID_ENERGY,
    WB_COUNTRY_API,
    WB_WDI_INDICATORS,
    _get_json,
    wdi_url,
)

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
            meta, payload = _get_json(wdi_url(code, page))
            rows += [
                row
                for row in payload or []
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


def main() -> None:
    print(f"recording fixtures for {len(COUNTRIES)} countries into {FIXTURE_DIR}")
    record_owid(OWID_CO2)
    record_owid(OWID_ENERGY)
    record_wb_country()
    record_wdi()
    record_eurostat()
    print("done — commit tests/fixtures/ingest/")


if __name__ == "__main__":
    main()
