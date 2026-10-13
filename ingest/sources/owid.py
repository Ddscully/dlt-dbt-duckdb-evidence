"""OWID CO2 and energy: two whole-file CSV downloads.

The simplest source here — no auth, no pagination, no incremental state. Both
land with `write_disposition="replace"`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import dlt
import polars as pl

from ingest import fixtures

if TYPE_CHECKING:
    import pyarrow as pa

OWID_CO2 = "https://raw.githubusercontent.com/owid/co2-data/master/owid-co2-data.csv"
OWID_ENERGY = "https://raw.githubusercontent.com/owid/energy-data/master/owid-energy-data.csv"


def _csv_source(url: str) -> str:
    """The CSV to hand Polars: the live URL, or a fixture path when offline."""
    return str(fixtures.path_for(url)) if fixtures.enabled() else url


def _read_csv(url: str) -> pa.Table:
    """The whole file as one Arrow table, typed by Polars.

    `infer_schema_length=None` scans every row, so a sparse numeric column (empty
    for its first rows) is typed as a number, not a string. Arrow rather than
    Python dicts, as `retail_invoice_lines` yields: dlt then writes the types
    Polars inferred instead of re-inferring them cell by cell. Measured live
    through this pipeline, download included, `owid_co2` (50,411 rows by 79
    columns) went from 7.2 s to 2.4 s and `owid_energy` from 5.3 s to 1.7 s,
    with the same rows, columns and types for both files. The
    one column an Arrow load lacks is dlt's `_dlt_id`, which nothing reads;
    `_dlt_load_id` is added by `ingest.pipeline.build_pipeline`.
    """
    return pl.read_csv(_csv_source(url), infer_schema_length=None).to_arrow()


@dlt.resource(name="owid_co2", write_disposition="replace")
def owid_co2():
    yield _read_csv(OWID_CO2)


@dlt.resource(name="owid_energy", write_disposition="replace")
def owid_energy():
    yield _read_csv(OWID_ENERGY)
