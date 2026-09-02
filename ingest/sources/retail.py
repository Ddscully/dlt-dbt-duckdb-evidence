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
#
# The only source here that is a *bulk file drop* rather than an API: one 45 MB
# zip holding one workbook of two sheets, republished when the curator revises it
# and otherwise static. Everything downstream of that shape — the cache, the
# workbook reader, load-time rather than fetch-time partitions — follows from it.
RETAIL_ARCHIVE = "https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip"
RETAIL_WORKBOOK_NAME = "online_retail_II.xlsx"

# The extract's own bounds — first and last transaction month. Constants rather
# than a query because the orchestration layer needs them to *define* the
# partitions, before any data has been loaded to read them from. They are safe to
# hardcode in a way no other source's bounds would be: this is a closed archive,
# the study period ended on 2011-12-09, and a revision of the file would be a
# re-transcription of the same two years rather than an extension of them.
# `tests/test_ingest.py` checks them against the recorded fixture, so a
# re-recording that widened the window would fail rather than silently leave
# months with no partition to land in.
RETAIL_FIRST_MONTH = "2009-12"
RETAIL_LAST_MONTH = "2011-12"

# (invoice, line_number). The source has no line identifier at all, so this is
# assigned from file position — see `retail_sql` for why content can't do it and
# what that costs.
RETAIL_PRIMARY_KEY = ("invoice", "line_number")

# Rows per Arrow batch handed to dlt. Small enough that peak memory is flat over
# a full 1.07M-row load, large enough that the per-batch overhead disappears.
RETAIL_BATCH_ROWS = 100_000

# Declared rather than inferred, for the same reason `wb_wdi`'s are: this is the
# second resource whose schema isn't dropped and re-inferred each run, and a
# partition that happened to contain only whole prices would infer `bigint` for
# `unit_price` and shunt the next 1.25 into a `unit_price__v_double` variant.
# `customer_id` is text on purpose — it is an identifier that happens to look
# numeric, and the one thing nobody will ever do to it is arithmetic.
#
# No `nullable: False` on the key columns, unlike `wb_wdi` and `ecb_fx_rates` —
# though not for the reason it first looks like. Every load logs a hint-mismatch
# warning naming `invoice` and `line_number`, because Arrow fields are nullable
# by construction and dlt's schema says they aren't; removing the explicit hints
# does *not* silence it, since `primary_key` marks its own columns non-nullable
# anyway. The warning is unavoidable for an Arrow resource with a key, and it is
# harmless: dlt's hint wins and the column lands NOT NULL. They are left out
# because they would be redundant, and the assertion is worth more in dbt
# regardless — `not_null` there stores the offending rows rather than logging.
RETAIL_COLUMNS: dict[str, TColumnSchema] = {
    "invoice": {"data_type": "text"},
    "line_number": {"data_type": "bigint"},
    "stock_code": {"data_type": "text"},
    "description": {"data_type": "text"},
    "quantity": {"data_type": "bigint"},
    # `timezone: False`, and it is not cosmetic. dlt's default `timestamp` maps to
    # TIMESTAMP WITH TIME ZONE, which reads a naive value as UTC and renders it in
    # the reader's zone: a till receipt stamped 07:45 came back as
    # `2009-12-01 08:45:00+01:00` on a CET machine and would come back as 07:45 in
    # CI, so the same workbook produced a different warehouse depending on where
    # it was built. These are wall-clock shop times with no zone attached and no
    # instant to preserve — the only faithful storage is a naive timestamp.
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

    Everything else in this file is a request whose response *is* the data. This
    is a 45 MB zip that has not changed since 2023 and never will — the study
    period closed in 2011 — so re-fetching it per load would be 45 MB of
    politeness to no one, and re-fetching it per *partition* would make a
    twenty-five-month backfill a gigabyte of identical downloads. It is cached in
    `data/cache/` instead; the directory is gitignored and safe to delete.

    Under fixtures the zip is the recorded one and no download happens at all,
    which is the same code path with a different byte source — the unzip, the
    sheet discovery and the `all_varchar` read all still run.

    **A fixture run caches into its own subdirectory**, and that is not tidiness.
    The two workbooks have the same name and the same shape, so a fixture run
    writing to the shared path would leave the 30k-row slice sitting there as
    `online_retail_II.xlsx` — and the next *real* run would find it, skip the
    download and load the slice into the real warehouse with no error anywhere.
    Same failure the `_fixtures` pipeline-name suffix exists to prevent one layer
    down, and the same one an absolute `WAREHOUSE_PATH` prevents in the tests.

    **The extract is keyed on the archive's content**, which is the same failure
    one level further in. Keyed on the directory alone — "it exists, return it" —
    the cache had no way to notice that the archive underneath it had changed, so
    `just record-fixtures` rewriting the recorded zip left the *previous* slice
    sitting in `fixtures/` and every fixture test then passed against data the
    repo no longer held. A digest in the path makes a re-record a cache miss.
    Stale digest directories are left behind rather than pruned; they cost disk
    in a gitignored cache, and deleting is the one thing this function should not
    be doing on its own.
    """
    cache = Path(cache_dir()) / ("fixtures" if fixtures.enabled() else "live")
    cache.mkdir(parents=True, exist_ok=True)

    # The download moved above the cache check, because the check now needs the
    # archive to hash. It is still at most one download: the live archive is
    # itself cached, and the fixture one is in the repo.
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

    A function rather than a constant because the month filter is the partition:
    `('2010-01', '2010-03')` loads that window and nothing else. Filtering here
    rather than after the read is what makes a partition cheap — DuckDB still
    parses the whole workbook (a spreadsheet has no index), but only the selected
    rows are materialised, converted and handed to dlt.

    Three things happen in this SQL and each is deliberate:

    * **`line_number` is assigned from file position**, because the source has no
      line identifier and 34,337 rows are exact duplicates of another row — same
      invoice, product, quantity, price and timestamp. They cannot be told apart
      by content, so content cannot key them. File order can, and it is stable:
      `preserve_insertion_order` is set explicitly rather than left to DuckDB's
      default, because that default is what the key's determinism rests on and a
      future release changing it would corrupt the merge silently rather than
      loudly. `tests/test_ingest.py` reads the fixture twice and compares.
    * **Every column is cast from text exactly once**, here, where the failure is
      visible. See `modern_data_stack.workbook` for why the read is all-text.
    * **Nothing is cleaned.** `Invoice` keeps its `C` and `A` prefixes, quantities
      keep their signs, `Customer ID` keeps its 243,007 blanks and `StockCode`
      keeps both `M` and `m`. The landing table is what the publisher sent; the
      taxonomy those prefixes encode is a modelling decision and belongs in
      staging, not in a cast.
    """
    ts = excel_serial_to_timestamp('"InvoiceDate"')
    # Filtered on the underlying expression, not on the `invoice_month` alias, and
    # in WHERE rather than QUALIFY. Both matter: WHERE runs before the window, so
    # a month filter costs nothing, and it cannot renumber a kept invoice's lines
    # because it only ever removes whole invoices — a month boundary never falls
    # inside one.
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
    """A UK gift retailer's order lines, 2009-12 to 2011-12 — the first grain
    below a country, and the first row here that is a thing somebody *did*.

    Every other source in this file is a published statistic: an annual national
    aggregate somebody else computed. This is 1,067,371 raw events, and what it
    buys is the entire vocabulary those aggregates can't reach — a customer, an
    order, a return, a cohort, a basket, a margin.

    It is also messy in ways that are load-bearing rather than incidental, which
    is most of why it was chosen over a synthetic set. The three prefixes on
    `Invoice` are a taxonomy nobody documented (45,330 sales, 8,292 `C`
    cancellations, 6 `A` bad-debt adjustments); 3,457 negative-quantity rows sit
    on *sale* invoices and are not returns at all; and `StockCode` carries
    postage, bank charges, Amazon fees and a literal `TEST001` alongside the
    products. All of that is landed exactly as sent and sorted out in staging.

    Yielded as Arrow record batches, not dicts: a million rows through Python
    dictionaries costs about a minute and a couple of GB, and dlt writes Arrow
    straight to Parquet without ever building the row objects.

    **`to_arrow_reader`, never `.arrow()`.** `DuckDBPyRelation.arrow()` returns a
    streaming `RecordBatchReader` whose default batch is 1,000,000 rows, and a
    caller that treats it as a table gets the first batch and no warning — this
    resource silently landed exactly 1,000,000 of its 1,067,371 rows until the
    round number gave it away. The reader is the right object anyway, because
    consuming it in batches is what keeps peak memory flat, but it has to be
    *iterated*. `tests/test_ingest.py` pins the row count against the workbook's
    own so a batch-size change can't reintroduce it.
    """
    con = workbook.connect()
    con.execute(f"create or replace view sheets as {workbook.sheets_sql(retail_workbook())}")
    yield from con.sql(retail_sql(months)).to_arrow_reader(RETAIL_BATCH_ROWS)
