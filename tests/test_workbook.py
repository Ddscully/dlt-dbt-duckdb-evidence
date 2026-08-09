"""The spreadsheet reader — `modern_data_stack.workbook`.

These are the tests the retail fixture deliberately can't be: it holds one sheet,
because DuckDB's xlsx writer replaces a file per `COPY` and a two-sheet workbook
would have to be assembled by hand at the zip level. So the multi-sheet
behaviour is pinned here instead, against workbooks built for the purpose — and
more sharply than a fixture could, because these can be *wrong* on purpose.
"""

from __future__ import annotations

import pytest

from modern_data_stack import workbook


def _write_sheet(con, path, sheet: str, select: str) -> None:
    con.execute(f"copy ({select}) to '{path}' (format xlsx, header true, sheet '{sheet}')")


@pytest.fixture
def con():
    return workbook.connect()


def test_sheet_names_are_read_from_the_container(con, tmp_path):
    """No Excel library, no parse of the sheet data — just the workbook's own XML."""
    book = tmp_path / "one.xlsx"
    _write_sheet(con, book, "Year 2009-2010", "select 1 as a")
    assert workbook.sheet_names(book) == ["Year 2009-2010"]


def test_a_workbook_with_no_worksheets_raises(tmp_path):
    """Rather than returning an empty list, which reads downstream as an empty
    *source* — a load that succeeds and is zero rows."""
    import zipfile

    book = tmp_path / "empty.xlsx"
    with zipfile.ZipFile(book, "w") as archive:
        archive.writestr("xl/workbook.xml", "<workbook></workbook>")
    with pytest.raises(ValueError, match="no worksheets"):
        workbook.sheet_names(book)


def test_sheets_are_unioned_by_name_not_by_position(con, tmp_path):
    """The failure this prevents is the one that makes hand-converted
    spreadsheets untrustworthy: a publisher reorders the columns on the second
    sheet and every value silently shifts one column across.

    Built as two separate files because DuckDB can't write two sheets into one —
    the SQL is what's under test, and it doesn't care that they're separate.
    """
    first = tmp_path / "a.xlsx"
    second = tmp_path / "b.xlsx"
    _write_sheet(con, first, "S1", "select 'x' as code, 10 as qty")
    # Same two columns, opposite order.
    _write_sheet(con, second, "S2", "select 20 as qty, 'y' as code")

    sql = " union all by name ".join(
        workbook.sheets_sql(book, [sheet]) for book, sheet in ((first, "S1"), (second, "S2"))
    )
    rows = {r[0]: r[1] for r in con.sql(f"select code, qty from ({sql})").fetchall()}
    assert rows == {"x": "10", "y": "20"}


def test_a_sheet_missing_a_column_is_padded_with_null_not_rejected(con, tmp_path):
    """The limit of union-by-name, pinned because it is not the intuitive answer.

    A sheet that has lost a column entirely does *not* raise — DuckDB pads it
    with NULL and returns the rows, so a truncated sheet arrives as data with a
    hole in it. There is no strict mode to switch on, so this is a known property
    to guard downstream (`not_null` in staging) rather than a bug to fix here.
    Written as a test so that "we thought it raised" can never be the reason a
    short load ships.
    """
    first = tmp_path / "a.xlsx"
    second = tmp_path / "b.xlsx"
    _write_sheet(con, first, "S1", "select 'x' as code, 10 as qty")
    _write_sheet(con, second, "S2", "select 'y' as code")

    sql = " union all by name ".join(
        workbook.sheets_sql(book, [sheet]) for book, sheet in ((first, "S1"), (second, "S2"))
    )
    rows = dict(con.sql(f"select code, qty from ({sql})").fetchall())
    assert rows == {"x": "10", "y": None}


def test_every_cell_is_read_as_text(con, tmp_path):
    """`all_varchar`, and it is not a convenience.

    A column of integers with one `C489449` in it is what a real invoice column
    looks like, and DuckDB's inference fails the *whole read* on it. Reading as
    text makes the read total and moves the type decision into SQL.
    """
    book = tmp_path / "mixed.xlsx"
    _write_sheet(con, book, "S1", "select '489434' as invoice union all select 'C489449'")
    rows = con.sql(workbook.sheets_sql(book)).fetchall()
    assert {r[0] for r in rows} == {"489434", "C489449"}


def test_excel_serial_converts_through_the_1900_leap_year_bug(con):
    """1899-12-30, not 1899-12-31.

    Excel believes 1900 was a leap year, so a naive epoch is a day out for every
    date after February 1900 — which is every date anyone has. 40148.322916 is
    the first timestamp in the retail workbook and is 2009-12-01 07:45.
    """
    sql = workbook.excel_serial_to_timestamp("'40148.3229166666'")
    assert str(con.sql(f"select {sql}").fetchone()[0]) == "2009-12-01 07:45:00"


def test_extract_member_refuses_an_archive_with_two_candidates(tmp_path):
    """Picking the first alphabetically is how half a source goes missing."""
    import zipfile

    archive = tmp_path / "two.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("a.xlsx", "one")
        z.writestr("b.xlsx", "two")
    with pytest.raises(ValueError, match="exactly one"):
        workbook.extract_member(archive, tmp_path, ".xlsx")


def test_extract_member_returns_the_single_member(tmp_path):
    import zipfile

    archive = tmp_path / "one.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("nested/book.xlsx", "payload")
    out = workbook.extract_member(archive, tmp_path / "dest", ".xlsx")
    assert out.name == "book.xlsx"
    assert out.read_bytes() == b"payload"
