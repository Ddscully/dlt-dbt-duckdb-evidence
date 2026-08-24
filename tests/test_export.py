"""Unit tests for the publishable artifact (`scripts/export_warehouse.py`).

These build a miniature warehouse in a tmp dir rather than reading
`data/warehouse.duckdb` — no network, no fixtures, no dependency on whether the
real pipeline has been run. What's worth asserting is the packaging contract, not
the numbers: which schemas ship, that the manifest describes what's on disk, and
that the `staging` views still resolve after the database is copied.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

from modern_data_stack import export as _export
from modern_data_stack.db import scalar
from scripts.export_warehouse import default_tag, release_notes, run

# `raw` and `main` must not ship as Parquet; `staging`/`marts`/`analytics` must.
# The view is written fully qualified the way dbt-duckdb writes it — that's what
# makes the catalog name load-bearing when the database is copied.
SETUP = """
create schema raw;
create schema staging;
create schema marts;
create schema analytics;

create table raw.owid_co2 as
    select * from (values ('USA', 2020, 4.7), ('FRA', 2020, 0.3)) t(iso3, year, co2);
create table raw._dlt_loads as
    select * from (values ('1785315112.98', now())) t(load_id, inserted_at);

create view warehouse.staging.stg_co2 as
    select iso3 as country_iso3, year, co2 as co2_mt from warehouse.raw.owid_co2;
create view warehouse.staging.stg_country as
    select * from (values ('USA', 'North America'), ('FRA', 'Europe & Central Asia'))
    t(country_iso3, region);
create table marts.fct_emissions_energy as select * from staging.stg_co2;
create table analytics.co2_intensity as select country_iso3, year, 1.0 as intensity
    from staging.stg_co2;
"""


@pytest.fixture
def warehouse(tmp_path: Path) -> Path:
    """A miniature warehouse. The file name matters: DuckDB names the catalog
    after the stem, and the views above reference `warehouse`."""
    path = tmp_path / "src" / "warehouse.duckdb"
    path.parent.mkdir()
    con = duckdb.connect(str(path))
    con.execute(SETUP)
    con.close()
    return path


@pytest.fixture
def export(warehouse: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """The manifest, with `out` added for convenience.

    The salt is required rather than defaulted (`tests/test_privacy.py` is where
    that is asserted), so even a fixture warehouse holding no personal data at
    all has to supply one. That is the policy working: there is no path through
    the exporter that publishes without a decision about identifiers having been
    made, including this one.
    """
    monkeypatch.setenv("PII_SALT", "a-salt-for-tests")
    out = tmp_path / "export"
    manifest = run(str(warehouse), str(out), tag="data-1999-12-31", repo="acme/demo")
    manifest["out"] = out
    return manifest


def test_only_the_modelled_layers_ship_as_parquet(export: dict):
    """`raw` is dlt's landing zone and dbt's seed schema is an implementation
    detail; both are reachable in the DuckDB file and neither is a flat file."""
    assert [t["table"] for t in export["tables"]] == [
        "analytics.co2_intensity",
        "marts.fct_emissions_energy",
        "staging.stg_co2",
        "staging.stg_country",
    ]
    assert not list(export["out"].glob("raw__*.parquet"))


def test_manifest_describes_the_files_on_disk(export: dict):
    """Row counts, sizes and checksums are the artifact's only documentation, so
    a manifest that disagrees with the bytes is worse than none."""
    with duckdb.connect() as con:
        for table in export["tables"]:
            path = export["out"] / table["file"]
            assert table["bytes"] == path.stat().st_size
            assert table["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
            assert table["rows"] == scalar(con, f"select count(*) from '{path}'")

    # What run() returned is what it wrote (the `out` key is the test's own).
    written = json.loads((export["out"] / "manifest.json").read_text())
    assert written == {k: v for k, v in export.items() if k != "out"}


def test_year_coverage_is_reported_only_where_there_is_a_year(export: dict):
    tables = {t["table"]: t for t in export["tables"]}
    assert tables["staging.stg_co2"]["years"] == [2020, 2020]
    assert "years" not in tables["staging.stg_country"]  # the country dimension


def test_the_period_column_reaches_every_table(tmp_path: Path):
    """`export()` threads its period column down to each table rather than
    leaving `export_table`'s `"year"` default in charge. This project's period
    *is* `year`, so the parameter only shows up for a reuser — whose manifest
    would otherwise be missing coverage bounds for every table, with no error.

    The manifest key stays `years` whatever the column is: `release_notes` reads
    it, and renaming it per project would make the published manifest's shape
    depend on the period, which consumers parse.
    """
    src = tmp_path / "src" / "warehouse.duckdb"
    src.parent.mkdir()
    con = duckdb.connect(str(src))
    con.execute("create schema marts")
    con.execute("create table marts.readings as select * from (values (1), (12)) t(month)")
    con.close()

    manifest = _export.export(
        str(src),
        str(tmp_path / "out"),
        schemas=("marts",),
        attribution="none",
        release_notes=lambda *_: "",
        period_column="month",
    )
    assert manifest["tables"][0]["years"] == [1, 12]


def test_sha256sums_is_checkable(export: dict):
    """`sha256sum -c` format: hash, two spaces, bare filename."""
    lines = (export["out"] / "SHA256SUMS").read_text().splitlines()
    assert lines[0].split("  ") == [export["warehouse"]["sha256"], "warehouse.duckdb"]
    assert len(lines) == len(export["tables"]) + 1


def test_the_copied_warehouse_keeps_its_views_working(export: dict):
    """The trap this export exists around: dbt's views store fully-qualified SQL
    against a catalog named `warehouse`, so the copy has to keep that file name.
    Copy it to `snapshot.duckdb` and every view raises `Catalog "warehouse" does
    not exist` — for us here and for anyone who ATTACHes it under another alias.
    """
    con = duckdb.connect()
    con.execute(f"attach '{export['out'] / 'warehouse.duckdb'}' as warehouse (read_only)")
    assert con.execute("select count(*) from warehouse.staging.stg_co2").fetchone() == (2,)
    # raw survives the copy too — the DuckDB file is the complete artifact.
    assert con.execute("select count(*) from warehouse.raw.owid_co2").fetchone() == (2,)


def test_provenance_records_the_load_not_just_the_run(export: dict):
    """An export of a stale warehouse should look stale: `data_loaded_at` comes
    from dlt's `_dlt_loads`, not from the clock."""
    assert export["data_loaded_at"] is not None
    assert export["duckdb_version"] == duckdb.__version__


def test_release_notes_link_the_latest_alias_and_the_pinned_tag(export: dict):
    notes = release_notes(export, "acme/demo", export["tag"])
    assert "releases/latest/download/warehouse.duckdb" in notes
    assert "releases/download/data-1999-12-31/" in notes
    # The alias warning is the one thing a consumer can't work out for themselves.
    assert "AS warehouse (READ_ONLY)" in notes
    assert "CC BY 4.0" in notes


def test_exporting_into_the_warehouse_directory_is_refused(warehouse: Path):
    """The copy keeps the source's file name, so exporting beside the source
    would unlink the warehouse it was asked to package."""
    with pytest.raises(ValueError, match="overwrite the source warehouse"):
        run(str(warehouse), str(warehouse.parent))
    assert warehouse.exists()


def test_tags_are_dated():
    assert default_tag(datetime(2026, 7, 30, tzinfo=UTC)) == "data-2026-07-30"
