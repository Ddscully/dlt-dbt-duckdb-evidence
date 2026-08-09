"""Offline fixtures for the ingest layer — this project's routes.

`ingest.pipeline` fetches from six live endpoints. That makes CI a test of
whether OWID, the World Bank and Eurostat happen to be up, which is not what a
pull request is asking. Setting ``INGEST_FIXTURES=1`` swaps every fetch for a
checked-in payload recorded from those same endpoints, so the *whole* pipeline —
dlt schema inference, dbt, Polars, the asset checks — runs deterministically and
offline.

The fixtures are trimmed to a representative set of countries; see
`scripts/record_fixtures.py`, which is what produced them and what re-records
them when a source changes shape. The FX series has no country in it and is kept
whole — gzipped, because 3.6 MB of JSON compresses to 831 kB and every
discontinuity in it is something a model is tested against.

The mechanism (and the reasoning behind it) lives in
`modern_data_stack.fixtures`. What's here is the URL-to-file map, which is the
only part that's about these five sources. The OWID fixtures are gzipped CSV
rather than Parquet so they still go through `pl.read_csv` with
`infer_schema_length=None`, and the JSON fixtures are the API's own response
body — the parsing gotchas that bite in production are exercised in CI too.
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
