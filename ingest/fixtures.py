"""Offline fixtures for the ingest layer — this project's routes.

`ingest.pipeline` fetches from every endpoint in `_ROUTES` below. That makes CI a
test of whether OWID, the World Bank and Eurostat happen to be up, which is not
what a pull request is asking. Setting ``INGEST_FIXTURES=1`` swaps every fetch
for a checked-in payload recorded from those same endpoints, so the *whole*
pipeline — dlt schema inference, dbt, Polars, the asset checks — runs
deterministically and offline.

The fixtures are trimmed to a representative set of countries; see
`scripts/record_fixtures.py`, which is what produced them and what re-records
them when a source changes shape. The FX series has no country in it and is kept
whole — gzipped, because 3.6 MB of JSON compresses to 831 kB and every
discontinuity in it is something a model is tested against.

The mechanism (and the reasoning behind it) lives in
`modern_data_stack.fixtures`. What's here is the URL-to-file map, which is the
only part that's about this project's own sources. The OWID fixtures are gzipped
CSV rather than Parquet so they still go through `pl.read_csv` with
`infer_schema_length=None`, and the JSON fixtures are the API's own response
body — the parsing gotchas that bite in production are exercised in CI too.

**Neither paragraph counts the sources, deliberately.** The first said "six live
endpoints" and the third "these five sources", while `_ROUTES` holds eight routes
across six publishers — both stale, and already stale before the source that made
them wrong arrived. `tests/test_documented_counts.py` scans markdown and YAML and
never a `.py` docstring, so no guard here can go red on a number; naming the list
is what survives the next source instead.
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
    # The only fixture that is not a response body but a *file* — the retail
    # source is a zip holding a workbook, and the fixture is a smaller zip
    # holding a smaller workbook. Same container, so the unzip, the sheet
    # discovery and the all-text read are all exercised in CI; a bare `.xlsx`
    # here would skip the first two, and a CSV would skip all three.
    (re.compile(r"archive\.ics\.uci\.edu/static/public/502/"), "retail_online_retail_ii.zip"),
    # No capture for the coordinates or the date window, for the same reason the
    # FX route captures no date range: the recorded payload is one window over
    # every location, and the resource merges on `(country_iso3, weather_date)`,
    # so re-landing a day replaces it. It matters more here than there — a live
    # run asks for one window per calendar year, and all of them resolve to this
    # one file, which is exactly what keeps a fixture run from making 19 requests
    # against a rate limit that is not being enforced against it.
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
