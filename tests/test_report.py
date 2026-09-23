"""The Evidence layer's seams: what its source queries read, and where its pages
are expected to land.

No Node and no warehouse here — building the site needs both, and `just test` has
neither. What this pins is the two places the site can silently drift out of step
with the rest of the repo.
"""

from __future__ import annotations

from pathlib import Path

import pytest

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


def test_the_published_dbt_docs_do_not_track_visitors(monkeypatch):
    """dbt's docs page hands the manifest's `send_anonymous_usage_stats` to a
    Snowplow tracker, and dbt's default is on. Measured: with the flag left
    alone, the rendered page loads `sp.js` from CloudFront, and every visitor
    to the public site reports their page views to dbt Labs; with it off, no
    script is injected. The lakehouse path rides along because a relative one
    is refused inside the docs build, which Dagster runs without `just`."""
    monkeypatch.setenv("DBT_SEND_ANONYMOUS_USAGE_STATS", "true")
    monkeypatch.delenv("LAKEHOUSE_DIR", raising=False)
    env = build_report.dbt_docs_env()
    assert env["DBT_SEND_ANONYMOUS_USAGE_STATS"] == "false"
    assert Path(env["LAKEHOUSE_DIR"]).is_absolute()


def test_the_index_links_to_the_dbt_docs_as_a_file():
    """The link has to be relative, for the Pages base path, and `rel="external"`:
    without it the prerender crawler follows `dbt/` before `build_report` has
    written it and fails the build with a 404."""
    index = (build_report.PAGES_DIR / "index.md").read_text()
    assert f'<a href="{build_report.DBT_DOCS_ROUTE}/" rel="external">' in index


def test_site_root_defaults_to_the_build_directory(monkeypatch, tmp_path: Path):
    """Unset, nothing about the build changes: `just serve` on a laptop serves
    `reports/build` directly, and a copy of a site onto itself is the one case
    that must not happen."""
    monkeypatch.delenv("SITE_ROOT", raising=False)
    build = tmp_path / "build"
    assert build_report.site_root(build) == build
    assert build_report.publish_to(build, build_report.site_root(build)) is False


def test_publishing_replaces_the_contents_of_the_site_root(tmp_path: Path):
    """The *contents*, never the directory: under compose the destination is a
    mount point shared with nginx, and `rmtree` on one fails with EBUSY. So a
    stale page has to go without the directory going."""
    build = tmp_path / "build"
    (build / "assets").mkdir(parents=True)
    (build / "index.html").write_text("new")
    (build / "assets" / "app.js").write_text("js")

    served = tmp_path / "site"
    served.mkdir()
    (served / "index.html").write_text("old")
    (served / "gone.html").write_text("a page that was deleted upstream")
    (served / "stale").mkdir()
    (served / "stale" / "chunk.js").write_text("orphan")

    inode = served.stat().st_ino
    assert build_report.publish_to(build, served) is True

    assert served.stat().st_ino == inode, "the destination directory itself was replaced"
    assert (served / "index.html").read_text() == "new"
    assert (served / "assets" / "app.js").read_text() == "js"
    assert not (served / "gone.html").exists()
    assert not (served / "stale").exists()


def test_publishing_refuses_a_site_root_that_contains_the_build(tmp_path: Path):
    """`SITE_ROOT=/app/reports` names the build's parent. Emptying it would
    delete the pages and source queries the site is built from, so the refusal
    has to come before anything is removed."""
    reports = tmp_path / "reports"
    build = reports / "build"
    build.mkdir(parents=True)
    (build / "index.html").write_text("site")
    (reports / "pages").mkdir()
    (reports / "pages" / "index.md").write_text("# a page")

    with pytest.raises(ValueError, match="contain one another"):
        build_report.publish_to(build, reports)
    assert (reports / "pages" / "index.md").exists()
    with pytest.raises(ValueError, match="contain one another"):
        build_report.publish_to(build, build / "served")


def test_site_root_reads_the_environment(monkeypatch, tmp_path: Path):
    """Read from the environment rather than passed in, because the two things
    that serve the site — `just serve`'s http.server and nginx under compose —
    already agree on it there."""
    monkeypatch.setenv("SITE_ROOT", str(tmp_path / "srv"))
    assert build_report.site_root(tmp_path / "build") == tmp_path / "srv"
