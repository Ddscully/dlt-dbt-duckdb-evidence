"""Every URL the pipeline can build, for the tests that must cover each one.

A module of its own rather than a constant in `test_fixtures.py`, because two
test files read it: the fixture guards, and the release's attribution check in
`test_export.py`. A test file that imports another cannot even be collected
once any import the other makes breaks.
"""

from __future__ import annotations

from ingest.sources.ecb import (
    fx_start_date,
    fx_url,
)
from ingest.sources.eurostat import EU_ELEC_PRICES_API
from ingest.sources.owid import (
    OWID_CO2,
    OWID_ENERGY,
)
from ingest.sources.retail import RETAIL_ARCHIVE
from ingest.sources.weather import weather_url
from ingest.sources.worldbank import (
    WB_COUNTRY_API,
    WB_WDI_INDICATORS,
    wdi_url,
)

ALL_URLS = (
    [OWID_CO2, OWID_ENERGY, WB_COUNTRY_API, EU_ELEC_PRICES_API, RETAIL_ARCHIVE]
    + [wdi_url(code) for code in WB_WDI_INDICATORS]
    # Both branches of `fx_start_date`, because the FX resource builds a different
    # URL on a first load (the whole series) than on every later one (a lookback
    # window off the watermark). One entry would leave the other shape untested,
    # and the end date is today's, so neither is a constant.
    + [fx_url(fx_start_date(None)), fx_url(fx_start_date("2026-01-15"))]
    # A one-location stand-in rather than `weather_locations()`, which is a
    # *fetch* — it reads the World Bank country payload, and this module is
    # imported by `just test`, which has no network and no fixture flag set. The
    # substitution is safe because the weather route captures nothing: every
    # archive URL resolves to the same file whatever coordinates or window it
    # carries, so a real location list could not select a different fixture.
    + [weather_url([("DEU", 52.5235, 13.4115)], "2007-01-01", "2007-12-31")]
)
