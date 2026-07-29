"""Offline fixtures for the ingest layer.

`ingest.pipeline` fetches from five live endpoints. That makes CI a test of
whether OWID, the World Bank and Eurostat happen to be up, which is not what a
pull request is asking. Setting ``INGEST_FIXTURES=1`` swaps every fetch for a
checked-in payload recorded from those same endpoints, so the *whole* pipeline —
dlt schema inference, dbt, Polars, the asset checks — runs deterministically and
offline.

The fixtures are trimmed to a representative set of countries; see
`scripts/record_fixtures.py`, which is what produced them and what re-records
them when a source changes shape.

Two deliberate choices:

* **Fixtures sit behind the same code path, not beside it.** The OWID fixtures
  are gzipped CSV (not Parquet) so they still go through `pl.read_csv` with
  `infer_schema_length=None` — the type-inference gotcha that bites in
  production is exercised in CI too. The JSON fixtures are the API's own
  response body, so `_get_json`'s callers parse exactly what they parse live.
* **Resolution is explicit, not a URL hash.** A missing mapping raises rather
  than silently falling through to the network, and `tests/test_fixtures.py`
  asserts every URL the pipeline can build resolves to a file that exists.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "ingest"

ENV_VAR = "INGEST_FIXTURES"

# (pattern, fixture filename). The WDI entry captures the indicator code, which
# is the only part of the URL space that varies per request.
_ROUTES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"owid/co2-data/.*\.csv$"), "owid_co2.csv.gz"),
    (re.compile(r"owid/energy-data/.*\.csv$"), "owid_energy.csv.gz"),
    (re.compile(r"api\.worldbank\.org/v2/country\?"), "wb_country.json"),
    (
        re.compile(r"api\.worldbank\.org/v2/country/all/indicator/(?P<code>[A-Z0-9.]+)"),
        "wb_wdi_{code}.json",
    ),
    (re.compile(r"eurostat/.*/data/nrg_pc_204"), "eu_elec_prices.json"),
]


def enabled() -> bool:
    """True when the pipeline should read fixtures instead of the network."""
    return os.environ.get(ENV_VAR, "").lower() in {"1", "true", "yes"}


def path_for(url: str) -> Path:
    """Map a source URL to its fixture file.

    Raises rather than returning None: an unmapped URL means the fixture set has
    drifted from the pipeline, and falling back to the network would turn that
    into a silently-online CI run.
    """
    for pattern, template in _ROUTES:
        match = pattern.search(url)
        if match:
            return FIXTURE_DIR / template.format(**match.groupdict())
    raise KeyError(f"no fixture mapped for {url!r} — see scripts/record_fixtures.py")
