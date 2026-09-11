"""Offline fixtures for the ingest layer — this project's routes.

`INGEST_FIXTURES=1` swaps every fetch in `_ROUTES` for a checked-in payload
recorded from the same endpoint, so the whole pipeline — dlt schema inference,
dbt, Polars, the asset checks — runs offline and deterministically, and a CI
failure means the repo broke rather than a publisher being down.

The fixtures are trimmed to a representative set of countries by
`scripts/record_fixtures.py`, which also re-records them. Each is the source's
own format (gzipped CSV for OWID, the API's response body for the JSON sources,
a zip for retail), so the parsing paths production uses run in CI too.

The mechanism is `modern_data_stack.fixtures`; this module is the URL-to-file
map for this project's sources.
"""

from __future__ import annotations

import re
from pathlib import Path

from modern_data_stack import fixtures as _fixtures
from modern_data_stack.fixtures import DEFAULT_ENV_VAR as ENV_VAR
from modern_data_stack.paths import project_root

FIXTURE_DIR = project_root() / "tests" / "fixtures" / "ingest"

# (pattern, fixture filename). The WDI entry captures the indicator code, which
# is the only part of the URL space that varies per request.
_ROUTES: list[_fixtures.Route] = [
    (re.compile(r"owid/co2-data/.*\.csv$"), "owid_co2.csv.gz"),
    (re.compile(r"owid/energy-data/.*\.csv$"), "owid_energy.csv.gz"),
    (re.compile(r"api\.worldbank\.org/v2/country\?"), "wb_country.json"),
    (
        re.compile(r"api\.worldbank\.org/v2/country/all/indicator/(?P<code>[A-Z0-9.]+)"),
        "wb_wdi_{code}.json",
    ),
    (re.compile(r"eurostat/.*/data/nrg_pc_204"), "eu_elec_prices.json"),
    # No capture for the date range: the recorded payload is the *whole* series,
    # and an incremental run asking for a ten-day window gets all of it back.
    # That is safe because the resource merges on (rate_date, quote_currency).
    (re.compile(r"api\.frankfurter\.dev/v1/\d{4}-\d{2}-\d{2}\.\."), "ecb_fx_rates.json.gz"),
    # A smaller zip holding a smaller workbook, so the unzip, sheet discovery
    # and all-text read all run in CI.
    (re.compile(r"archive\.ics\.uci\.edu/static/public/502/"), "retail_online_retail_ii.zip"),
    # No capture for coordinates or dates, as with FX: one recorded window over
    # every location, and the merge on `(country_iso3, weather_date)` makes
    # re-landing a day harmless.
    (re.compile(r"archive-api\.open-meteo\.com/v1/archive\?"), "om_weather_daily.json.gz"),
]


def enabled() -> bool:
    """True when the pipeline should read fixtures instead of the network."""
    return _fixtures.enabled(ENV_VAR)


def path_for(url: str) -> Path:
    """Map a source URL to its fixture file.

    Raises rather than returning None: an unmapped URL means the fixture set has
    drifted from the pipeline, and falling back to the network would turn that
    into a silently-online CI run.
    """
    try:
        return _fixtures.resolve(url, _ROUTES, FIXTURE_DIR)
    except KeyError as exc:
        raise KeyError(f"no fixture mapped for {url!r} — see scripts/record_fixtures.py") from exc
