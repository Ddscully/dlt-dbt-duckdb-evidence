"""OWID CO2 and energy: two whole-file CSV downloads.

The simplest source here — no auth, no pagination, no incremental state. Both
land with `write_disposition="replace"`.
"""

from __future__ import annotations

import dlt
import polars as pl

from ingest import fixtures

OWID_CO2 = "https://raw.githubusercontent.com/owid/co2-data/master/owid-co2-data.csv"
OWID_ENERGY = "https://raw.githubusercontent.com/owid/energy-data/master/owid-energy-data.csv"


def _csv_source(url: str) -> str:
    """The CSV to hand Polars: the live URL, or a fixture path when offline."""
    return str(fixtures.path_for(url)) if fixtures.enabled() else url


# infer_schema_length=None scans the whole file so sparse numeric columns
# (empty for the first rows) are typed as numbers, not strings.
@dlt.resource(name="owid_co2", write_disposition="replace")
def owid_co2():
    yield pl.read_csv(_csv_source(OWID_CO2), infer_schema_length=None).to_dicts()


@dlt.resource(name="owid_energy", write_disposition="replace")
def owid_energy():
    yield pl.read_csv(_csv_source(OWID_ENERGY), infer_schema_length=None).to_dicts()
