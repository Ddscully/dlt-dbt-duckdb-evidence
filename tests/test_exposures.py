"""The declared consumers, checked against the ones that actually exist.

`dbt/models/_exposures.yml` says which models each Evidence page and the monthly
data release depend on. Nothing in dbt can verify that — an exposure is an
assertion about the *outside* of the project, so a stale one is invisible: `dbt
build` stays green, `dbt ls --select +exposure:*` keeps answering, and the answer
is quietly wrong. These tests are the other half of it, resolving page → source
query → warehouse table from the Evidence tree and comparing.

No warehouse and no dbt manifest here (`just test` has neither — the manifest is
gitignored and built later in CI), so everything below reads the yml and the SQL
as text.
"""

from __future__ import annotations

import re

import yaml

from modern_data_stack.paths import project_root
from scripts import build_report

EXPOSURES_YML = project_root() / "dbt" / "models" / "_exposures.yml"
MARTS_DIR = project_root() / "dbt" / "models" / "marts"

_REF = re.compile(r"ref\(\s*'([a-z_0-9]+)'\s*\)")


def exposures() -> dict[str, dict]:
    """`{"evidence_retail": {…}, …}`, straight out of the yml."""
    parsed = yaml.safe_load(EXPOSURES_YML.read_text())
    return {exposure["name"]: exposure for exposure in parsed["exposures"]}


def declared_models(name: str) -> set[str]:
    """The model names one exposure's `depends_on` refs."""
    return {
        match.group(1)
        for entry in exposures()[name]["depends_on"]
        for match in _REF.finditer(entry)
    }


def page_models() -> dict[str, set[str]]:
    """`{"retail": {"fct_retail_order_line", …}, …}` — the dbt models each page reads."""
    return {
        page: {
            build_report.TABLE_TO_DBT_MODEL[t]
            for t in tables
            if t in build_report.TABLE_TO_DBT_MODEL
        }
        for page, tables in build_report.page_tables().items()
    }


def test_each_page_exposure_names_exactly_what_its_charts_read():
    """`evidence_<slug>` ⇔ the models `pages/<slug>.md` reaches through its queries.

    The failure this prevents: add a chart reading a new mart, and without this the
    exposure keeps describing the old dependency set. `dbt ls --select
    +exposure:evidence_currency` then omits the model the page just started
    reading, which is exactly when someone is relying on that command to decide
    whether a change is safe.
    """
    declared = {
        name.removeprefix("evidence_"): declared_models(name)
        for name in exposures()
        if name.startswith("evidence_")
    }
    expected = {page: models for page, models in page_models().items() if models}
    assert declared == expected


def test_the_pages_with_no_exposure_are_the_two_that_cannot_have_one():
    """Two pages carry no exposure, for opposite reasons, and both are asserted.

    `pipeline.md` reads three tables (`analytics.pipeline_sources`, `_tables`,
    `_tests`), every one written by Polars, downstream of dbt and unknown to it —
    so there is nothing an exposure could depend on and `depends_on` cannot be
    empty. `index.md` reads *no* tables: it is a routing page, prose and links
    only, which is also why no figure on it can go stale by hand.

    Telling the two apart is the point. Both look identical through
    `page_models()` — an empty set — but only one of them would still be correct
    after growing a chart on a mart. The table counts below are what separate
    "dbt cannot describe this" from "there is nothing here to describe".
    """
    tables = build_report.page_tables()
    pages_without_models = {page for page, models in page_models().items() if not models}
    assert pages_without_models == {"index", "pipeline"}

    assert tables["index"] == set(), "the routing page grew a query; give it an exposure"
    assert tables["pipeline"], "pipeline.md should still read the Polars tables"

    assert "evidence_index" not in exposures()
    assert "evidence_pipeline" not in exposures()


def test_the_tables_no_exposure_can_name_are_exactly_the_polars_outputs():
    """The measured size of the gap above: what the pages read that dbt does not build.

    `TABLE_TO_ASSET_KEY` is the Dagster-side map that covers those tables, so this
    asserts the two halves of the site's dependency story partition it cleanly —
    every table a page reads is either declared to dbt as an exposure dependency or
    claimed by an asset key, and none is claimed by neither or by both.
    """
    invisible = {
        table
        for tables in build_report.page_tables().values()
        for table in tables
        if table not in build_report.TABLE_TO_DBT_MODEL
    }
    assert invisible == set(build_report.TABLE_TO_ASSET_KEY)


def test_the_release_exposure_names_every_mart():
    """The data release ships every table in `marts`, so the exposure has to list them all.

    `scripts/export_warehouse.py` iterates the schema rather than a table list, so a
    new mart is published the moment it is built — silently, to consumers outside
    this repo who cannot be paged. That is the one dependency here nobody can
    discover by reading the site, which is why it is asserted rather than described.
    """
    declared = declared_models("published_data_release")
    # `fct_emissions_energy_v1.sql` / `_v2.sql` are two files and one model: an
    # exposure names the model, and `ref()` without a `v=` resolves to the latest
    # version. Both relations ship in the release, and both are covered by the one
    # declaration.
    marts = {re.sub(r"_v\d+$", "", sql.stem) for sql in MARTS_DIR.glob("*.sql")}
    assert marts - declared == set(), "mart published by the release but not declared"
    # `stg_country` is the one non-mart in the list — see the comment in the yml.
    assert declared - marts == {"stg_country"}
