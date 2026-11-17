"""UCI Online Retail II: order lines from a bulk workbook.

The one source that is a file drop rather than an API, and the one whose grain
is below a country. The download is cached by content digest, and the resource
yields Arrow batches straight out of DuckDB.
"""

from __future__ import annotations

import hashlib
import zipfile
import zlib
from pathlib import Path

import dlt
from dlt.common.schema.typing import TColumnSchema

from ingest import fixtures, http
from modern_data_stack import env, workbook
from modern_data_stack.paths import cache_dir
from modern_data_stack.workbook import excel_serial_to_timestamp, extract_member


def whole_zip(path: Path) -> None:
    """Raise `ValueError` unless `path` is a zip whose every member reads back
    with the checksum it was stored with.

    `zipfile.is_zipfile` alone finds only the directory at the end of the file,
    so a body whose member data was damaged in transit would pass it; `testzip`
    reads every member. Reading a damaged *compressed* member can fail before
    the checksum is compared, in zlib, so those errors count as damage too. A
    `ValueError`, so `http.get_file` retries it. The message names no file: the
    one checked may be a temporary one, gone by the time anyone reads it.
    """
    if not zipfile.is_zipfile(path):
        with path.open("rb") as head:
            raise ValueError(f"not a zip; it starts {head.read(80)!r}")
    try:
        with zipfile.ZipFile(path) as archive:
            damaged = archive.testzip()
    except (zipfile.BadZipFile, EOFError, zlib.error, NotImplementedError, RuntimeError) as exc:
        raise ValueError(f"not a whole zip: {exc}") from exc
    if damaged is not None:
        raise ValueError(f"a damaged member, {damaged}")


# UCI Online Retail II, CC BY 4.0: one static zip of one workbook, hence the
# cache and load-time partitions. https://archive.ics.uci.edu/dataset/502/online+retail+ii
RETAIL_ARCHIVE = "https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip"
RETAIL_WORKBOOK_NAME = "online_retail_II.xlsx"

# Constants, because partitions are defined before any load and the archive is
# closed; `tests/test_ingest.py` checks them against the fixture.
RETAIL_FIRST_MONTH = "2009-12"
RETAIL_LAST_MONTH = "2011-12"

# `line_number` is file position: the source has no line id (`retail_sql`).
RETAIL_PRIMARY_KEY = ("invoice", "line_number")

# Keeps peak memory flat over a full load without per-batch overhead.
RETAIL_BATCH_ROWS = 100_000

# Declared: a merge keeps dlt's widen-only schema, so whole prices would infer
# bigint. `primary_key` makes the keys NOT NULL; the hint-mismatch warning every
# load logs for them is harmless.
RETAIL_COLUMNS: dict[str, TColumnSchema] = {
    "invoice": {"data_type": "text"},
    "line_number": {"data_type": "bigint"},
    "stock_code": {"data_type": "text"},
    "description": {"data_type": "text"},
    "quantity": {"data_type": "bigint"},
    # Zoneless till times; dlt's default zone shifts them by the machine's offset.
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

    The extract is checked on every read, as the archive is: a cache serves
    whatever reached it, and an unzip interrupted before extracts were written
    whole left a short workbook under the real name.
    """
    cache = Path(cache_dir()) / ("fixtures" if fixtures.enabled() else "live")
    cache.mkdir(parents=True, exist_ok=True)

    # The archive comes first because the extract is keyed on its digest.
    if fixtures.enabled():
        archive = fixtures.path_for(RETAIL_ARCHIVE)
    else:
        archive = cache / "online_retail_ii.zip"
        # A whole extract proves its archive was whole, so the warm path, which
        # every load takes, skips reading 45 MB to check it. Any other archive is
        # checked, and fetched again if it fails: one can reach the cache by a
        # route that never checked it.
        if archive.exists():
            extracted = _extract_path(cache, archive)
            if _whole_extract(extracted):
                return extracted
        http.get_file(RETAIL_ARCHIVE, archive, check=whole_zip, reuse=True)

    extracted = _extract_path(cache, archive)
    if _whole_extract(extracted):
        return extracted
    return extract_member(archive, extracted.parent, ".xlsx")


def _whole_extract(path: Path) -> bool:
    """Whether `path` holds a workbook that was written to the end.

    An .xlsx is a zip, whose directory is its last bytes, so a short or empty
    file fails `is_zipfile`. That is the cheap half of `whole_zip`: `zipfile`
    checks a member's CRC as `extract_member` reads it to the end, and refuses a
    mismatch before the extract is renamed into place, on the fixture branch as
    on the live one. So only truncation afterwards is left to find.
    """
    return path.exists() and zipfile.is_zipfile(path)


def _extract_path(cache: Path, archive: Path) -> Path:
    """Where the workbook extracted from `archive` lives: keyed on its digest."""
    return cache / _content_digest(archive) / RETAIL_WORKBOOK_NAME


def _typed_cte() -> str:
    """The `source` and `typed` CTEs over the `sheets` view: every column cast
    from text exactly once, plus `file_row`, the file position the line numbers
    and the sheet rule are read from. Shared, so the guard judges the very rows
    `retail_sql` lands."""
    ts = excel_serial_to_timestamp('"InvoiceDate"')
    return f"""
        source as (
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
        )"""


def retail_sql(months: tuple[str, str] | None = None) -> str:
    """The SQL that turns the workbook into `raw.retail_invoice_lines`.

    `months` is the partition filter: DuckDB still parses the whole workbook,
    but only the selected rows are converted and handed to dlt.

    * **Each invoice comes from one sheet.** The workbook's two sheets overlap:
      both hold 1–9 December 2010, line for line. Each sheet's year starts on
      1 December, so those days belong to the later sheet and the earlier one
      runs nine days past its year; `deduped` keeps an invoice from the later
      sheet only. The copies differ only in the order of 769 lines, so the
      choice decides which order numbers them, and those lines' keys name
      different lines than in a load from before the rule. It is not a
      `distinct`: 11,812 lines of the landed table repeat another exactly, and
      those are genuine repeated lines on one invoice. The rule is safe only
      because the two copies are identical, which `retail_sheet_conflicts_sql`
      checks before every load.
    * `line_number` comes from file position: the source has no line id, and
      exact repeats leave nothing in the content to tell lines apart.
      `preserve_insertion_order` is set explicitly (in
      `modern_data_stack.workbook`) because the key's determinism rests on it;
      `tests/test_ingest.py` reads the fixture twice and compares.
    * Every column is cast from text exactly once, in `_typed_cte` — see
      `modern_data_stack.workbook` for why the read is all-text.
    * Nothing is cleaned: invoice prefixes, signs and blank customer ids land as
      sent, and staging decides what they mean.
    """
    # In WHERE, before the window, so it cannot renumber a kept invoice's lines:
    # it removes whole invoices only, since no invoice spans a month boundary.
    # After `deduped`, so an overlapping invoice is cut to one sheet first.
    where = ""
    if months is not None:
        where = f"where strftime(invoice_ts, '%Y-%m') between '{months[0]}' and '{months[1]}'"
    return f"""
        with {_typed_cte()},
        deduped as (
            select * from typed
            qualify sheet_name = last_value(sheet_name) over (
                partition by invoice order by file_row
                rows between unbounded preceding and unbounded following)
        ),
        filtered as (
            select *, strftime(invoice_ts, '%Y-%m') as invoice_month
            from deduped
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


def retail_sheet_conflicts_sql() -> str:
    """The invoices held by more than one sheet whose copies differ.

    Each copy is compared as a multiset: its lines are grouped on every column
    with a count, so a repeated line counts as many times as it appears and
    line order is ignored (the real copies differ in order and nothing else).
    A group, count included, missing from any sheet that holds the invoice is a
    conflict. One row per conflicting invoice, ordered, with how many of its
    line groups differ.

    The columns come from `typed` with a `*`, never a list: a column a list left
    out would drop from the comparison, and a copy differing only there would be
    dropped as identical.
    """
    return f"""
        with {_typed_cte()},
        copies as (
            select * exclude (file_row), count(*) as n
            from typed
            group by all
        ),
        sheets_holding as (
            select invoice, count(distinct sheet_name) as n_sheets
            from typed
            group by invoice
            having count(distinct sheet_name) > 1
        ),
        groups as (
            select * exclude (sheet_name), count(*) as n_holding
            from copies
            where invoice in (select invoice from sheets_holding)
            group by all
        )
        select invoice, count(*) as differing_groups
        from groups
        join sheets_holding using (invoice)
        where n_holding < n_sheets
        group by invoice
        order by invoice
    """


def retail_full_reload_requested() -> bool:
    """`INGEST_RETAIL_FULL=1` drops the landing table and reloads the workbook.

    The merge key is `(invoice, line_number)`, so a reload under a new rule
    updates keys 1..N of an invoice and leaves any higher ones it no longer
    yields: a landing zone loaded before the sheet rule keeps the overlap's
    second copies until this runs. The workflows build from empty.
    """
    return env.flag("INGEST_RETAIL_FULL")


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
    # A table, not a view: the guard and the load each read every row, and as a
    # view each would parse the workbook again, which doubled the resource's
    # time on the real file for no saving in peak memory.
    con.execute(f"create temp table sheets as {workbook.sheets_sql(retail_workbook())}")
    # Dropping one sheet's copy is safe only while the copies are identical, so
    # a workbook whose sheets disagree about an invoice is refused, not loaded.
    conflicts = con.sql(retail_sheet_conflicts_sql()).fetchall()
    if conflicts:
        raise RuntimeError(
            f"{len(conflicts)} invoice(s) appear in more than one sheet with different "
            f"lines, first {conflicts[0][0]!r}; keeping one sheet's copy would lose data"
        )
    yield from con.sql(retail_sql(months)).to_arrow_reader(RETAIL_BATCH_ROWS)
