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
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import duckdb
import pytest
from test_fixtures import ALL_URLS

from modern_data_stack import export as _export
from modern_data_stack.db import scalar
from modern_data_stack.export import storage_version
from scripts.export_warehouse import (
    ATTRIBUTION,
    MAX_PUBLISHED_STORAGE_VERSION,
    MIN_READER_VERSION,
    default_tag,
    release_notes,
    run,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

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


# --- the storage format of the published file -------------------------------
#
# DuckDB 2.0 ships a new default storage format. Nothing in `pyproject.toml`
# caps `duckdb>=1.1`, and `versioning-strategy: lockfile-only` means the bump
# arrives as one line of a grouped monthly Dependabot PR. What follows splits
# the guard across the two moments that matter: the *toolchain* test runs on
# every PR and catches the bump before it merges; the *artifact* tests catch a
# file that should not be uploaded.


def test_the_installed_duckdb_still_writes_the_format_the_release_promises(tmp_path: Path):
    """The tripwire, and the only one of these that fires on a dependency bump.

    A plain `duckdb.connect()` — no `STORAGE_VERSION` — is what the exporter
    does, so what it writes by default *is* the published format. Everything
    else here reads a file this same binary produced, which is exactly why none
    of them can notice the default moving: a repo's tests all write and read
    with one version of the library.

    `<=` rather than `==` because a lower number is the more compatible file;
    only an increase strands a reader.
    """
    path = tmp_path / "default.duckdb"
    con = duckdb.connect(str(path))
    con.execute("create table t as select 1 as a")
    con.close()

    written = storage_version(path)
    assert written <= MAX_PUBLISHED_STORAGE_VERSION, (
        f"DuckDB {duckdb.__version__} now writes storage version {written} by default, "
        f"above the {MAX_PUBLISHED_STORAGE_VERSION} the release notes promise "
        f"(readable by DuckDB {MIN_READER_VERSION}+). Raising the ceiling strands "
        f"every client older than the new format — decide it, don't merge it."
    )


def test_the_manifest_records_the_format_and_not_only_the_writer(export: dict):
    """`duckdb_version` answers "who wrote this"; a consumer is asking "can I
    open it", which is a different question with a different answer — 1.5.5
    writes the format 0.10.0 reads. Both ship, and the format is measured off
    the bytes that were uploaded rather than off the connection that made them.
    """
    published = export["out"] / "warehouse.duckdb"
    assert export["storage_version"] == storage_version(published)
    assert export["storage_version"] <= MAX_PUBLISHED_STORAGE_VERSION
    assert export["duckdb_version"] == duckdb.__version__


@pytest.mark.parametrize(
    "refused",
    [False, True],
    ids=["publishes-at-the-ceiling", "refuses-one-above-it"],
)
def test_the_ceiling_is_inclusive(warehouse: Path, tmp_path: Path, refused: bool):
    """Both sides of the boundary, because `>` and `>=` are the slip here and a
    one-sided test passes under either. The fixture warehouse is written by the
    installed DuckDB, so its format is the ceiling itself: exporting *at* that
    number must succeed and one below it must refuse.
    """
    on_disk = storage_version(warehouse)
    limit = on_disk - 1 if refused else on_disk
    out = tmp_path / f"out-{limit}"

    def do_export() -> dict:
        return _export.export(
            str(warehouse),
            str(out),
            schemas=("marts",),
            attribution="none",
            release_notes=lambda *_: "",
            max_storage_version=limit,
        )

    if refused:
        with pytest.raises(ValueError, match="storage"):
            do_export()
        # The guard fires after the database has been copied — that copy is what
        # it measured — so the directory is not empty. What it does guarantee is
        # that no *release* was assembled around the file: a caller that ignored
        # the exception would find nothing publishable to upload.
        assert not (out / "manifest.json").exists()
        assert not (out / "SHA256SUMS").exists()
        assert not list(out.glob("*.parquet"))
    else:
        assert do_export()["storage_version"] == on_disk


def test_a_file_that_is_not_a_duckdb_database_is_named_as_such(tmp_path: Path):
    """The header read is positional, so a short or foreign file would otherwise
    come back as a plausible integer rather than an error."""
    path = tmp_path / "not-a-database.parquet"
    path.write_bytes(b"PAR1" + b"\0" * 32)
    with pytest.raises(ValueError, match="not a DuckDB database file"):
        storage_version(path)


def test_the_release_notes_state_a_minimum_reader_version(export: dict):
    """The old line named the writer and said "older clients may not read the
    storage format", which is unmeasured and — as it turns out — pessimistic:
    the file is readable by every DuckDB back to 0.10.0. A consumer cannot check
    this for themselves without downloading the file first.
    """
    notes = release_notes(export, "acme/demo", export["tag"])
    assert f"DuckDB from {MIN_READER_VERSION} on" in notes
    assert str(export["storage_version"]) in notes


# --------------------------------------------------------------------------- #
# Attribution — the licence obligation the release actually carries
# --------------------------------------------------------------------------- #

# Which publisher each *fetch* host's data belongs to. Neither string is
# derivable from the other, which is the whole reason this map exists rather
# than a hostname comparison: attribution names the publisher, not the CDN or
# the API gateway in front of them. OWID is fetched from
# `raw.githubusercontent.com` and credited at `github.com/owid`; the World Bank
# from `api.worldbank.org` and credited at `data.worldbank.org`; the euro rates
# arrive via `api.frankfurter.dev`, a third-party mirror ATTRIBUTION names
# *beside* the ECB rather than instead of it.
PUBLISHER_FOR_FETCH_HOST = {
    "raw.githubusercontent.com": "github.com/owid",
    "api.worldbank.org": "data.worldbank.org",
    "ec.europa.eu": "ec.europa.eu/eurostat",
    "api.frankfurter.dev": "frankfurter.dev",
    "archive.ics.uci.edu": "archive.ics.uci.edu/dataset/502",
    # Fetched from the archive subdomain, credited at the bare one — the same
    # host-is-not-publisher split as OWID and the World Bank above.
    "archive-api.open-meteo.com": "open-meteo.com",
}


def _readme_licence_section() -> str:
    """README's `## License` section, bounded at the next heading.

    Bounded rather than read to end-of-file even though it is currently last:
    a section appended after it would otherwise widen this test silently, and
    every URL in the new one would start counting as an attribution link.
    """
    after = (REPO_ROOT / "README.md").read_text().split("## License", 1)[1]
    return after.split("\n## ", 1)[0]


ATTRIBUTION_HEADER = ("Source", "Publisher", "Licence")


def _attribution_rows() -> list[tuple[str, ...]]:
    """The table's data rows, as `(source, publisher, licence)` cells.

    The header is matched by content and asserted rather than skipped by
    position. `rows[1:]` looks equivalent and is not: edit or delete the header
    line and the slice silently drops the first *source* instead, which is
    exactly the row nothing else in this file would mention. Found by mutation —
    removing the header left this test green.

    The column order is what separates publisher from licence, so asserting the
    header is also asserting that the two are still where the callers below
    think they are.
    """
    lines = [ln for ln in ATTRIBUTION.splitlines() if ln.startswith("|") and "---" not in ln]
    cells: list[tuple[str, ...]] = [
        tuple(c.strip() for c in ln.strip("|").split("|")) for ln in lines
    ]
    assert cells[:1] == [ATTRIBUTION_HEADER], (
        f"the attribution table's header is {cells[:1]}, expected {[ATTRIBUTION_HEADER]} — "
        "the column order is what tells a publisher from a licence"
    )
    return cells[1:]


def test_every_source_the_pipeline_fetches_is_attributed():
    """Adding a source without crediting its publisher is a licence breach.

    The releases redistribute other people's data — all of it CC BY 4.0, a
    Eurostat/ECB reuse policy or an EU reuse decision — and every one of those
    permits redistribution *on condition of attribution*. `ATTRIBUTION` is the
    single source of truth for both the shipped `ATTRIBUTION.md` and the release
    notes, and CLAUDE.md carries the instruction "keep it in step with the
    README's licence section when a source is added" with nothing enforcing it.

    Tied to `ALL_URLS` because that is already the authority for what the
    pipeline fetches, and it has its own guards (every URL resolves to a
    fixture, every route is reachable). Restating the source list here would be
    a third copy to drift.

    The CBAM seeds are the one credited row this cannot reach: they are
    transcribed from a regulation by `scripts/build_cbam_seeds.py` rather than
    fetched, so no URL represents them. That is a gap in coverage, not an
    exemption — the row is in the table and is checked by the licence test
    below.
    """
    fetched = {urlparse(url).netloc for url in ALL_URLS}

    unattributed = sorted(fetched - set(PUBLISHER_FOR_FETCH_HOST))
    assert not unattributed, (
        f"the pipeline fetches from {unattributed} and nothing says who publishes it — "
        "add the host to PUBLISHER_FOR_FETCH_HOST and the publisher to ATTRIBUTION"
    )
    # Its own assertion: swapping one host for another produces a gap *and* a
    # stale entry, the assert above wins, and the stale half is never measured.
    # Same finding as the `RAW_DESCRIPTIONS` and WDI-pivot guards.
    stale = sorted(set(PUBLISHER_FOR_FETCH_HOST) - fetched)
    assert not stale, (
        f"PUBLISHER_FOR_FETCH_HOST names hosts the pipeline no longer fetches: {stale}"
    )
    # Searched in the **Publisher column**, not across the whole document.
    # Substring-matching the lot passes on a licence link that happens to
    # contain the publisher's path: `ec.europa.eu/eurostat` sits inside the
    # Eurostat copyright-notice URL, so deleting Eurostat as a *source* left
    # this green. Found by mutation, not by review.
    publishers = " ".join(row[1] for row in _attribution_rows())
    uncredited = sorted(
        publisher for publisher in PUBLISHER_FOR_FETCH_HOST.values() if publisher not in publishers
    )
    assert not uncredited, (
        f"fetched from, but named in no Publisher cell of the attribution table: {uncredited}"
    )


def test_the_release_attribution_and_the_readme_agree_on_the_licences():
    """Two documents state the same licences and neither is generated.

    They are shaped differently on purpose — ATTRIBUTION is a table a
    downloader reads beside the data, README's `## License` is prose a visitor
    reads — so this compares what they *say*, not how they say it.

    Matching on the markdown **label or** the URL is what makes that possible
    without a vocabulary of known licences, which would itself go stale the
    first time a source arrived under a licence nobody had listed. It is also
    load-bearing rather than defensive: ATTRIBUTION writes
    `[CC BY 4.0](https://creativecommons.org/...)` and README writes the bare
    words "CC BY 4.0" with no link, so a URL-only comparison fails today on a
    difference that is entirely legitimate.
    """
    rows = _attribution_rows()
    links = [
        pair for row in rows for pair in re.findall(r"\[([^\]]+)\]\((https?://[^)]+)\)", row[2])
    ]
    section = _readme_licence_section()

    # Vacuity guards. Both extractions parse hand-written markdown, and a
    # pattern that stops matching passes by not looking.
    assert len(rows) >= 6, f"only {len(rows)} source rows found in the ATTRIBUTION table"
    assert len(links) >= 6, f"only {len(links)} licence links found in the ATTRIBUTION table"
    assert "MIT" in section and len(section) > 500, "README's License section did not parse"

    unstated = sorted(
        {label for label, url in links if label not in section and url not in section}
    )
    assert not unstated, (
        f"licences in the release attribution that README's License section never mentions, "
        f"by name or by link: {unstated}"
    )
    # The other direction, so deleting a source from the table alone is caught.
    # No allowlist is needed and that is worth knowing before someone adds one:
    # every http link in that section is currently also an attribution link.
    orphaned = sorted(
        url for url in set(re.findall(r"https?://[^\s)]+", section)) if url not in ATTRIBUTION
    )
    assert not orphaned, (
        f"README's License section links sources the release attribution does not: {orphaned}"
    )
