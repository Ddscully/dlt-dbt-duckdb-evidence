"""UCI Online Retail II: order lines from a bulk workbook.

The one source that is a file drop rather than an API, and the one whose grain
is below a country. The download is cached by content digest, and the resource
yields Arrow batches straight out of DuckDB.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import dlt
import requests
from dlt.common.schema.typing import TColumnSchema

from ingest import fixtures
from modern_data_stack import workbook
from modern_data_stack.paths import cache_dir
from modern_data_stack.workbook import excel_serial_to_timestamp, extract_member


def _download(url: str, dest: Path, *, timeout: int = 300, chunk: int = 1 << 20) -> Path:
    """Stream a large file to disk, writing to a temporary name first.

    The rename is the point: an interrupted download that left a short file under
    the real name would be indistinguishable from a complete one on the next run,
    and the cache would serve a truncated workbook forever. A partial write
    leaves a `.part` behind instead, which is retried and overwritten.
    """
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, stream=True, timeout=timeout) as resp:
        resp.raise_for_status()
        with tmp.open("wb") as out:
            for block in resp.iter_content(chunk_size=chunk):
                out.write(block)
    tmp.replace(dest)
    return dest


# UCI Online Retail II — a UK online gift retailer's transactions, 2009-12 to
# 2011-12, CC BY 4.0. https://archive.ics.uci.edu/dataset/502/online+retail+ii
# One 45 MB zip holding one two-sheet workbook, static unless the curator revises
# it — hence the cache, the workbook reader and load-time partitions.
RETAIL_ARCHIVE = "https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip"
RETAIL_WORKBOOK_NAME = "online_retail_II.xlsx"

# First and last transaction month. Constants because the partitions are defined
# before any data is loaded, and safe because the archive is closed (the study
# ended 2011-12-09). `tests/test_ingest.py` checks them against the fixture.
RETAIL_FIRST_MONTH = "2009-12"
RETAIL_LAST_MONTH = "2011-12"

# (invoice, line_number). The source has no line identifier at all, so this is
# assigned from file position — see `retail_sql` for why content can't do it and
# what that costs.
RETAIL_PRIMARY_KEY = ("invoice", "line_number")

# Rows per Arrow batch handed to dlt. Small enough that peak memory is flat over
# a full 1.07M-row load, large enough that the per-batch overhead disappears.
RETAIL_BATCH_ROWS = 100_000

# Declared because a merge resource keeps dlt's widen-only schema: a partition of
# whole prices would infer bigint for `unit_price` and send the next 1.25 into a
# `unit_price__v_double` variant. `customer_id` is text: an identifier that looks
# numeric.
#
# No `nullable: False` on the keys: `primary_key` already makes them NOT NULL.
# Every load logs a hint-mismatch warning for them (Arrow fields are nullable),
# with or without explicit hints; it is harmless.
RETAIL_COLUMNS: dict[str, TColumnSchema] = {
    "invoice": {"data_type": "text"},
    "line_number": {"data_type": "bigint"},
    "stock_code": {"data_type": "text"},
    "description": {"data_type": "text"},
    "quantity": {"data_type": "bigint"},
    # `timezone: False`: dlt's default is TIMESTAMP WITH TIME ZONE, which renders
    # a naive 07:45 till time as 08:45+01:00 on a CET machine and 07:45 in CI.
    # These are zoneless shop wall-clock times.
    "invoice_ts": {"data_type": "timestamp", "timezone": False},
    "invoice_month": {"data_type": "text"},
    "unit_price": {"data_type": "double"},
    "customer_id": {"data_type": "text"},
    "country": {"data_type": "text"},
    "sheet_name": {"data_type": "text"},
}


def _content_digest(path: Path) -> str:
    """A short content hash of a file, read in chunks rather than into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:12]


def retail_workbook() -> Path:
    """The extracted Online Retail II workbook, downloading it at most once.

    The zip is static, so it is cached in the gitignored `data/cache/` rather
    than fetched per load or per partition. Under fixtures the recorded zip is
    used and everything after the download still runs.

    Fixture and live runs cache into separate subdirectories: the workbooks share
    a name, and a fixture slice left at the live path would be loaded by the next
    real run without error. The extract is keyed on the archive's content digest,
    so a re-recorded fixture is a cache miss rather than the previous slice.
    Stale digest directories are left for `just clean`.
    """
    cache = Path(cache_dir()) / ("fixtures" if fixtures.enabled() else "live")
    cache.mkdir(parents=True, exist_ok=True)

    # The archive comes first because the extract is keyed on its digest.
    if fixtures.enabled():
        archive = fixtures.path_for(RETAIL_ARCHIVE)
    else:
        archive = cache / "online_retail_ii.zip"
        if not archive.exists():
            _download(RETAIL_ARCHIVE, archive)

    extracted = cache / _content_digest(archive) / RETAIL_WORKBOOK_NAME
    if extracted.exists():
        return extracted
    return extract_member(archive, extracted.parent, ".xlsx")


def retail_sql(months: tuple[str, str] | None = None) -> str:
    """The SQL that turns the workbook into `raw.retail_invoice_lines`.

    `months` is the partition filter: DuckDB still parses the whole workbook,
    but only the selected rows are converted and handed to dlt.

    * `line_number` comes from file position: the source has no line id and
      34,337 rows duplicate another exactly. `preserve_insertion_order` is set
      explicitly (in `modern_data_stack.workbook`) because the key's determinism
      rests on it; `tests/test_ingest.py` reads the fixture twice and compares.
    * Every column is cast from text exactly once, here — see
      `modern_data_stack.workbook` for why the read is all-text.
    * Nothing is cleaned: invoice prefixes, signs and blank customer ids land as
      sent, and staging decides what they mean.
    """
    ts = excel_serial_to_timestamp('"InvoiceDate"')
    # In WHERE, before the window, so it cannot renumber a kept invoice's lines:
    # it removes whole invoices only, since no invoice spans a month boundary.
    where = ""
    if months is not None:
        where = f"where strftime(invoice_ts, '%Y-%m') between '{months[0]}' and '{months[1]}'"
    return f"""
        with source as (
            select *, row_number() over () as file_row from sheets
        ),
        typed as (
            select
                "Invoice"                   as invoice,
                "StockCode"                 as stock_code,
                "Description"               as description,
                cast("Quantity" as bigint)  as quantity,
                {ts}                        as invoice_ts,
                cast("Price" as double)     as unit_price,
                "Customer ID"               as customer_id,
                "Country"                   as country,
                sheet_name,
                file_row
            from source
        ),
        filtered as (
            select *, strftime(invoice_ts, '%Y-%m') as invoice_month
            from typed
            {where}
        )
        select
            invoice,
            row_number() over (partition by invoice order by file_row) as line_number,
            stock_code,
            description,
            quantity,
            invoice_ts,
            invoice_month,
            unit_price,
            customer_id,
            country,
            sheet_name
        from filtered
    """


@dlt.resource(
    name="retail_invoice_lines",
    write_disposition="merge",
    primary_key=RETAIL_PRIMARY_KEY,
    columns=RETAIL_COLUMNS,
)
def retail_invoice_lines(months: tuple[str, str] | None = None):
    """A UK gift retailer's order lines, 2009-12 to 2011-12 — the one grain below
    a country. Landed exactly as sent; the `retail-models` skill has the
    prefixes, write-offs and non-product stock codes staging sorts out.

    Yielded as Arrow batches: a million rows as Python dicts would cost about a
    minute and a couple of GB. `to_arrow_reader`, never `.arrow()`: the latter
    is a reader whose default batch is 1,000,000 rows, and treating it as a table
    silently keeps only the first. `tests/test_ingest.py` pins the row count.
    """
    con = workbook.connect()
    con.execute(f"create or replace view sheets as {workbook.sheets_sql(retail_workbook())}")
    yield from con.sql(retail_sql(months)).to_arrow_reader(RETAIL_BATCH_ROWS)
