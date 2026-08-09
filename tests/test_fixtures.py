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

import pytest

from ingest import fixtures
from ingest.pipeline import (
    EU_ELEC_PRICES_API,
    OWID_CO2,
    OWID_ENERGY,
    RETAIL_ARCHIVE,
    WB_COUNTRY_API,
    WB_WDI_INDICATORS,
    retail_sql,
    wdi_url,
)
from modern_data_stack import fixtures as _fixtures, workbook

ALL_URLS = [OWID_CO2, OWID_ENERGY, WB_COUNTRY_API, EU_ELEC_PRICES_API, RETAIL_ARCHIVE] + [
    wdi_url(code) for code in WB_WDI_INDICATORS
]


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
            .df()
            .iloc[0]
        )

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
