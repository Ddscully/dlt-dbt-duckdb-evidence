"""Read a spreadsheet that arrives as a bulk file drop, not as an API response.

Most sources here are a URL that answers with CSV or JSON. Some are a zip
containing an `.xlsx`, published once and revised by re-publishing — a shape
common enough in commercial data (a vendor extract, a regulator's annex, a
finance export) to be worth handling properly rather than converting by hand.

Three decisions, all of which cost something to get wrong:

* **Read every cell as text, then cast deliberately.** A spreadsheet column has
  no type — it has whatever each cell happens to hold. DuckDB's `read_xlsx`
  infers one from the first rows and then *fails the whole read* on the first
  cell that disagrees: an invoice column of integers dies on row 180 at the
  first `C489449` cancellation. `all_varchar` makes the read total and moves
  every type decision into SQL, where it is visible and testable.
* **Discover the sheets; never hardcode them.** The names carry meaning a
  publisher revises (`Year 2009-2010` gains a `Year 2011-2012`), and a hardcoded
  list silently drops the new one — a load that succeeds and is short. They are
  read out of the container's own `xl/workbook.xml`, which needs no dependency,
  because a spreadsheet is a zip of XML.
* **Excel dates are serial numbers and its epoch is wrong on purpose.** Day 1 is
  1900-01-01, but Excel also believes 1900 was a leap year, so every date after
  February 1900 is one day further from the epoch than it should be. Counting
  from **1899-12-30** absorbs both, which is why that constant is here with an
  explanation rather than inline somewhere as a magic date.

Nothing here knows what is in the workbook; the caller supplies the path and
does the casting.
"""

from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path

import duckdb

# Excel's day 0. See the module docstring: 1899-12-30 rather than 1899-12-31
# because of the phantom 29 February 1900. Serial values carry the time of day
# as a fraction, so the conversion is seconds rather than days.
EXCEL_EPOCH = "1899-12-30"

_SHEET_NAME = re.compile(r"<sheet[^>]*\bname=\"([^\"]*)\"")


def sheet_names(path: Path | str) -> list[str]:
    """The worksheet names, in workbook order, read from the container's XML.

    An `.xlsx` is a zip of XML parts, and `xl/workbook.xml` lists the sheets — so
    this needs neither an Excel library nor a full parse of the (potentially
    very large) sheet data.
    """
    with zipfile.ZipFile(path) as archive:
        workbook = archive.read("xl/workbook.xml").decode("utf-8")
    names = _SHEET_NAME.findall(workbook)
    if not names:
        raise ValueError(f"no worksheets found in {path}")
    return names


def extract_member(archive_path: Path | str, dest_dir: Path | str, suffix: str) -> Path:
    """Unpack the single `suffix` member of a zip into `dest_dir`, returning it.

    Raises when the archive holds none or more than one, rather than picking:
    a publisher that starts shipping two workbooks has changed the contract, and
    quietly reading the first one alphabetically is how half a source goes
    missing without an error.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        members = [n for n in archive.namelist() if n.endswith(suffix)]
        if len(members) != 1:
            raise ValueError(f"expected exactly one {suffix} in {archive_path}, found {members}")
        target = dest_dir / Path(members[0]).name
        with archive.open(members[0]) as src, target.open("wb") as out:
            shutil.copyfileobj(src, out)
    return target


def sheets_sql(path: Path | str, sheets: list[str] | None = None) -> str:
    """SQL reading every sheet of a workbook as all-text, with a `sheet_name` column.

    A SQL string rather than a relation or a frame, for two reasons. The caller
    is about to cast columns and filter, and wants to do that in one query
    against its *own* connection — a relation belongs to the connection that made
    it and can't be joined across. And for a large workbook the alternative is
    materialising a million rows of Python strings before the first cast.

    The union is by column *name*, not position, so a publisher reordering
    columns between sheets can't silently shift the data one across — the failure
    mode that makes a hand-converted spreadsheet untrustworthy.

    **What it does not protect against is a sheet that is missing a column
    entirely.** `union all by name` pads that with NULL and returns rows, so a
    truncated sheet arrives as data with a hole in it rather than as an error.
    There is no DuckDB setting that makes it strict, so the guard has to be
    downstream: the caller's `not_null` tests are what turn a padded column into
    a failure, which is the reason `stg_retail_lines` tests columns that "cannot"
    be null. `tests/test_workbook.py` pins the padding behaviour so that this
    stays a known property rather than a discovery.
    """
    path = Path(path)
    sheets = sheets or sheet_names(path)
    selects = [
        f"select *, {_quote(sheet)} as sheet_name "
        f"from read_xlsx({_quote(str(path))}, sheet = {_quote(sheet)}, all_varchar = true)"
        for sheet in sheets
    ]
    return " union all by name ".join(selects)


def connect() -> duckdb.DuckDBPyConnection:
    """An in-memory connection with the `excel` extension loaded and row order pinned.

    `preserve_insertion_order` is set explicitly rather than left at DuckDB's
    default. A workbook has no key of its own, so file position is often the only
    thing that can distinguish two otherwise identical rows — and a caller that
    builds an identifier out of it is depending on this setting, not on a
    default that a future release is free to change.
    """
    con = duckdb.connect()
    con.execute("install excel; load excel; set preserve_insertion_order = true;")
    return con


def excel_serial_to_timestamp(column: str) -> str:
    """SQL casting an Excel date serial (as text) to a timestamp. See `EXCEL_EPOCH`."""
    return (
        f"timestamp '{EXCEL_EPOCH}' "
        f"+ to_seconds(cast(round(cast({column} as double) * 86400) as bigint))"
    )


def _quote(value: str) -> str:
    """A SQL string literal. These names come from a file, so they get escaped."""
    escaped = value.replace("'", "''")
    return f"'{escaped}'"
