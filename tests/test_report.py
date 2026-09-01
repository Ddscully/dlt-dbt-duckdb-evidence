"""The Evidence layer's seams: what its source queries read, and where its pages
are expected to land.

No Node and no warehouse here — building the site needs both, and `just test` has
neither. What this pins is the two places the site can silently drift out of step
with the rest of the repo.
"""

from __future__ import annotations

from pathlib import Path

from publish import build_report


def test_every_source_table_has_a_declared_owner():
    """Each table the source queries read is claimed by one of the dep maps.

    This is the check that keeps `reports/evidence_site` correctly ordered in the
    asset graph. Add `select * from marts.fct_something_new` as a source query
    without adding it to `TABLE_TO_DBT_MODEL`, and the site still builds — just
    from whatever copy of that mart happened to be on disk, with the graph in the
    Dagster UI still showing a tidy, complete lineage. The failure has no symptom
    until a number on the dashboard is a day old.
    """
    declared = set(build_report.TABLE_TO_DBT_MODEL) | set(build_report.TABLE_TO_ASSET_KEY)
    assert build_report.source_tables() == declared


def test_source_tables_parses_past_comments():
    """`latest_years.sql` names columns in a comment table; only real `from`/`join`
    targets should come back. Guards the regex, which is what the test above
    trusts."""
    tables = build_report.source_tables()
    assert "marts.fct_emissions_energy" in tables
    # `-- co2_mt 214 …` and friends live in that file's comment block
    assert not any("co2_mt" in table for table in tables)


def test_source_tables_reads_a_temp_directory(tmp_path: Path):
    (tmp_path / "a.sql").write_text(
        """
        -- from marts.commented_out
        with x as (select * from RAW.owid_co2)
        select * from x join analytics.co2_intensity using (year)
        """
    )
    assert build_report.source_tables(tmp_path) == {"raw.owid_co2", "analytics.co2_intensity"}


def test_page_routes_map_markdown_to_evidence_output():
    """`pages/index.md` -> `build/index.html`, `pages/x.md` -> `build/x/index.html`.

    Evidence's routing, restated so the asset check can assert against it. If a
    future Evidence release changes the layout, this fails here rather than as a
    check that reports every page missing.
    """
    routes = build_report.page_routes()
    assert routes, "no pages found under reports/pages/"
    assert routes["index"] == build_report.BUILD_DIR / "index.html"
    for slug, path in routes.items():
        if slug != "index":
            assert path == build_report.BUILD_DIR / slug / "index.html"


def test_page_routes_cover_the_pages_that_exist():
    """Every `.md` under `pages/` is a route — no page silently unaccounted for."""
    markdown = {
        p.relative_to(build_report.PAGES_DIR).with_suffix("").as_posix()
        for p in build_report.PAGES_DIR.rglob("*.md")
    }
    assert set(build_report.page_routes()) == markdown
