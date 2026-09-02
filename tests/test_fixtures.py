"""Guards that keep the recorded fixtures in step with the pipeline.

The failure this exists to prevent: someone adds a WDI indicator, or a URL
constant changes, and the fixture-backed CI job keeps passing against a payload
that no longer represents what the pipeline asks for.
"""

from __future__ import annotations

import gzip
import json
import re
import tempfile
from types import SimpleNamespace

import pytest

from ingest import fixtures, pipeline
from ingest.sources.ecb import (
    fx_start_date,
    fx_url,
)
from ingest.sources.eurostat import EU_ELEC_PRICES_API
from ingest.sources.owid import (
    OWID_CO2,
    OWID_ENERGY,
)
from ingest.sources.retail import (
    RETAIL_ARCHIVE,
    retail_sql,
)
from ingest.sources.weather import weather_url
from ingest.sources.worldbank import (
    WB_COUNTRY_API,
    WB_WDI_INDICATORS,
    wdi_url,
)
from modern_data_stack import fixtures as _fixtures, workbook

ALL_URLS = (
    [OWID_CO2, OWID_ENERGY, WB_COUNTRY_API, EU_ELEC_PRICES_API, RETAIL_ARCHIVE]
    + [wdi_url(code) for code in WB_WDI_INDICATORS]
    # Both branches of `fx_start_date`, because the FX resource builds a different
    # URL on a first load (the whole series) than on every later one (a lookback
    # window off the watermark). One entry would leave the other shape untested,
    # and the end date is today's, so neither is a constant.
    + [fx_url(fx_start_date(None)), fx_url(fx_start_date("2026-01-15"))]
    # A one-location stand-in rather than `weather_locations()`, which is a
    # *fetch* — it reads the World Bank country payload, and this module is
    # imported by `just test`, which has no network and no fixture flag set. The
    # substitution is safe because the weather route captures nothing: every
    # archive URL resolves to the same file whatever coordinates or window it
    # carries, so a real location list could not select a different fixture.
    + [weather_url([("DEU", 52.5235, 13.4115)], "2007-01-01", "2007-12-31")]
)


@pytest.mark.parametrize("url", ALL_URLS)
def test_every_pipeline_url_has_a_fixture(url):
    """Each URL the pipeline can build resolves to a file that exists."""
    path = fixtures.path_for(url)
    assert path.exists(), f"missing fixture {path.name} — run `just record-fixtures`"


def test_unmapped_url_raises_rather_than_falling_back_to_the_network():
    """A silent fall-through would turn 'offline CI' into 'CI that is online on
    Tuesdays'."""
    with pytest.raises(KeyError, match="no fixture mapped"):
        fixtures.path_for("https://example.test/something-new.json")


def test_a_broken_route_is_not_reported_as_a_missing_fixture(tmp_path):
    """`str.format` raises `KeyError` both for an unmapped URL and for a template
    naming something the pattern doesn't capture — and `path_for` rewrites
    `KeyError` as "no fixture mapped … see scripts/record_fixtures.py". For a URL
    that matched a route and then failed to format, that sends you off to
    re-record fixtures for a URL that was fine. Hence the different type.
    """
    routes = [(re.compile(r"ind/(?P<code>\S+)"), "wdi_{indicator}.json")]
    with pytest.raises(ValueError, match="not a named group"):
        _fixtures.resolve("https://example.test/ind/SP.POP.TOTL", routes, tmp_path)


def test_enabled_reads_the_env_var(monkeypatch):
    monkeypatch.delenv(fixtures.ENV_VAR, raising=False)
    assert fixtures.enabled() is False
    monkeypatch.setenv(fixtures.ENV_VAR, "1")
    assert fixtures.enabled() is True
    monkeypatch.setenv(fixtures.ENV_VAR, "0")
    assert fixtures.enabled() is False


def test_wdi_fixtures_cover_the_countries_the_recorder_claims():
    """Cheap sanity check on the recorded content: the WDI slice includes the
    ISO3 codes the OWID slice does, minus TWN — which the World Bank doesn't
    publish, and which is exactly why `country_overrides` exists."""
    from scripts.record_fixtures import COUNTRIES

    payload = json.loads(fixtures.path_for(wdi_url("SP.POP.TOTL")).read_text())
    found = {row["countryiso3code"] for row in payload[1]}
    assert set(COUNTRIES) - found == {"TWN"}


def test_owid_fixture_is_a_readable_csv_slice():
    from scripts.record_fixtures import COUNTRIES

    with gzip.open(fixtures.path_for(OWID_CO2), "rt") as fh:
        header = fh.readline().strip().split(",")
        iso_codes = {line.split(",")[header.index("iso_code")] for line in fh}
    assert "co2" in header and "year" in header
    assert iso_codes == set(COUNTRIES)


def test_retail_fixture_holds_every_shape_the_models_handle():
    """The retail slice is defined by shapes, not by a row count.

    A 4% random sample of 1M rows keeps the volume and loses the point: six
    bad-debt adjustments and one positive line on a cancellation invoice do not
    survive it, and those are exactly the rows `stg_retail_lines`' taxonomy
    exists for. `record_retail` selects each shape explicitly; this is what
    stops a future re-record from quietly dropping one.
    """
    archive = fixtures.path_for(RETAIL_ARCHIVE)
    assert archive.exists()

    with tempfile.TemporaryDirectory() as tmp:
        book = workbook.extract_member(archive, tmp, ".xlsx")
        con = workbook.connect()
        con.execute(f"create or replace view sheets as {workbook.sheets_sql(book)}")
        row = (
            con.sql(
                f"""select
                    count(*) as n_rows,
                    count(distinct invoice) as invoices,
                    count(distinct customer_id) as customers,
                    count(distinct invoice_month) as months,
                    sum(starts_with(invoice, 'A')::int) as adjustments,
                    sum(starts_with(invoice, 'C')::int) as cancellations,
                    sum((starts_with(invoice, 'C') and quantity > 0)::int) as positive_cancellations,
                    sum((quantity < 0 and not starts_with(invoice, 'C'))::int) as write_offs,
                    sum((customer_id is null)::int) as anonymous_lines,
                    sum((unit_price < 0)::int) as negative_prices,
                    sum((description is null)::int) as null_descriptions,
                    count(distinct case
                        when not regexp_matches(stock_code, '^[0-9]{{5}}')
                        then upper(stock_code) end) as non_product_codes
                from ({retail_sql()})"""
            )
            .pl()
            .row(0, named=True)
        )
        # `.pl()`, not `.df()`: pandas is not a dependency of this project. It
        # used to arrive as harlequin's grand-transitive, so this line worked
        # without anything declaring it. `.row(named=True)` gives a dict; the
        # namespace is what keeps the assertions below reading as `row.n_rows`.
        row = SimpleNamespace(**row)

    assert row.n_rows > 20_000, "too thin for the cohort and RFM models to say anything"
    assert row.months == 25, "every month must be present or a Dagster partition loads nothing"
    assert row.adjustments == 6, "all six bad-debt adjustments, the shape a sample destroys"
    assert row.positive_cancellations == 1, "the line that disproves 'C implies negative'"
    assert row.cancellations > 100
    assert row.write_offs > 50, "write-offs are what make 'negative quantity' not mean 'return'"
    assert row.anonymous_lines > 1_000, "22.8% of the source has no customer"
    assert row.negative_prices > 0, "only the A-prefixed adjustments have these"
    assert row.null_descriptions > 0
    assert row.non_product_codes > 20, "POST, DOT, M, AMAZONFEE, TEST001…"
    assert row.customers > 150, "cohort retention needs customers with real histories"


def _routes_matching(url: str) -> list[_fixtures.Route]:
    """Every route whose pattern claims `url`, in the order `resolve` walks them."""
    return [route for route in fixtures._ROUTES if route[0].search(url)]


def test_no_two_routes_claim_the_same_url():
    """A route the pipeline can never reach is dead weight that looks alive.

    `_fixtures.resolve` returns at the *first* pattern that matches, so a route
    added after one that already covers its URLs is silently shadowed — the
    fixture it names is recorded, committed, and never once served. The existing
    URL guard cannot see it: the URL still resolves, and still resolves to a file
    that exists, just not the intended one.

    Checked over `ALL_URLS` rather than by comparing patterns to each other,
    because regex containment is undecidable in general and the URLs the pipeline
    actually builds are the only ones that matter.
    """
    clashes = []
    for url in ALL_URLS:
        hits = [f"{pattern.pattern} -> {template}" for pattern, template in _routes_matching(url)]
        if len(hits) > 1:
            clashes.append(f"{url}\n      " + "\n      ".join(hits))

    assert not clashes, "a URL is claimed by more than one route:\n    " + "\n    ".join(clashes)


def test_every_route_is_reachable_from_some_pipeline_url():
    """`_ROUTES` and `ALL_URLS` are both maintained by hand; this is what makes
    them agree.

    It is the only check here that can see a *missing* `ALL_URLS` entry. The
    other two iterate URLs, so a source absent from the list is absent from them
    as well — which is how `ecb_fx_rates` sat outside every fixture test for the
    whole life of the FX source, with its route never once exercised.
    """
    reached = {template for url in ALL_URLS for _, template in _routes_matching(url)}
    unreachable = [template for _, template in fixtures._ROUTES if template not in reached]

    assert not unreachable, (
        f"routes no URL in ALL_URLS reaches: {unreachable} — either the pipeline stopped "
        f"building that URL, or ALL_URLS is missing it"
    )


def test_no_recorded_fixture_is_orphaned():
    """The reverse direction: a file in `tests/fixtures/ingest/` nothing serves.

    `record_fixtures.py` writes files and never deletes them, so dropping a
    source or renaming a WDI indicator leaves its payload behind. An orphan is
    harmless at runtime, which is exactly why it needs a test — it is committed
    data that nothing reads and that looks like coverage.
    """
    served = {fixtures.path_for(url).name for url in ALL_URLS}
    orphaned = {p.name for p in fixtures.FIXTURE_DIR.iterdir() if p.is_file()} - served

    assert not orphaned, (
        f"recorded fixtures nothing serves: {sorted(orphaned)} — delete them, or add "
        f"the URL that reads them to ALL_URLS"
    )


# A fixture is named after the resource it feeds. The exception is declared here
# rather than excused: the retail fixture is a real zip standing in for a real
# download, so it keeps the upstream archive's name instead of the resource's.
FIXTURE_NAME_EXCEPTIONS = {"retail_invoice_lines": "retail_online_retail_ii.zip"}


def test_every_resource_in_the_source_has_a_fixture_route():
    """A resource added without a route fails only in `just test-pipeline`, and
    reports `no fixture mapped for <url>` — the right message a tier too late,
    naming the URL rather than the resource that started building it.

    Enumerated off `public_indicators()` for the same reason
    `test_load_groups_covers_every_resource_in_the_source_exactly_once` is: the
    source is the thing that decides what gets fetched.
    """
    templates = [template for _, template in fixtures._ROUTES]

    for name in sorted(r.name for r in pipeline.public_indicators().resources.values()):
        declared = FIXTURE_NAME_EXCEPTIONS.get(name)
        if declared is not None:
            assert declared in templates, f"{name} declares fixture {declared!r}, which is no route"
        else:
            assert any(t.startswith(name) for t in templates), f"no fixture route for {name!r}"
