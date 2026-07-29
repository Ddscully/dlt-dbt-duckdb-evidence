"""Unit tests for the ingest layer's fetching and parsing.

Every HTTP call here is mocked. What's under test is the code that has actually
broken in this repo before — the retry/raise path, the WDI pagination loop, and
the Eurostat JSON-stat stride arithmetic — none of which is worth a live API
call to exercise. The end-to-end path lives in `just test-pipeline`.
"""

from __future__ import annotations

import json

import pytest
import requests
from dlt.extract.exceptions import ResourceExtractionError

from ingest import pipeline


class FakeResponse:
    """Minimal stand-in for `requests.Response`."""

    def __init__(self, payload=None, *, status: int = 200, body: str | None = None):
        self._payload = payload
        self._body = body
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")

    def json(self):
        if self._body is not None:
            return json.loads(self._body)  # raises ValueError on a non-JSON body
        return self._payload


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """The retry backoff is real seconds; tests shouldn't pay for it."""
    monkeypatch.setattr(pipeline.time, "sleep", lambda _: None)


@pytest.fixture(autouse=True)
def _fixtures_off(monkeypatch):
    """These tests exercise the network path, so make sure an inherited
    INGEST_FIXTURES from the caller's shell can't quietly bypass it."""
    monkeypatch.delenv(pipeline.fixtures.ENV_VAR, raising=False)


def _mock_get(monkeypatch, responses):
    """Serve `responses` in order, recording the URLs requested."""
    seen: list[str] = []
    queue = list(responses)

    def fake_get(url, timeout=None):
        seen.append(url)
        result = queue.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(pipeline.requests, "get", fake_get)
    return seen


# --------------------------------------------------------------------------- #
# _get_json
# --------------------------------------------------------------------------- #


def test_get_json_returns_parsed_payload(monkeypatch):
    _mock_get(monkeypatch, [FakeResponse({"ok": True})])
    assert pipeline._get_json("https://example.test/x") == {"ok": True}


def test_get_json_retries_then_succeeds(monkeypatch):
    seen = _mock_get(
        monkeypatch,
        [requests.ConnectionError("boom"), FakeResponse({"ok": True})],
    )
    assert pipeline._get_json("https://example.test/x") == {"ok": True}
    assert len(seen) == 2


def test_get_json_raises_on_persistent_http_error(monkeypatch):
    """A 500 must not be handed on as data.

    This is the regression the `raise_for_status()` was added for: without it an
    HTML error page parses to *something* and flows into the warehouse.
    """
    seen = _mock_get(monkeypatch, [FakeResponse(status=500)] * 3)
    with pytest.raises(RuntimeError, match="failed to fetch JSON"):
        pipeline._get_json("https://example.test/x")
    assert len(seen) == 3  # retried the configured number of times


def test_get_json_raises_on_non_json_body(monkeypatch):
    _mock_get(monkeypatch, [FakeResponse(body="<html>maintenance</html>")] * 3)
    with pytest.raises(RuntimeError, match="failed to fetch JSON"):
        pipeline._get_json("https://example.test/x")


# --------------------------------------------------------------------------- #
# wb_wdi
# --------------------------------------------------------------------------- #


def _wdi_page(page: int, pages: int, rows: list[dict]) -> list:
    return [{"page": page, "pages": pages, "per_page": 2, "total": 4}, rows]


def _wdi_row(iso3: str, date: str, value: float | None) -> dict:
    return {"countryiso3code": iso3, "date": date, "value": value}


def test_wb_wdi_follows_pagination(monkeypatch):
    """Every page is fetched, and the loop stops at the last one.

    The single-page version of this silently truncated the oldest indicators
    once the series outgrew one page, which is the failure this guards.
    """
    pages = {
        1: _wdi_page(1, 2, [_wdi_row("USA", "2023", 1.0)]),
        2: _wdi_page(2, 2, [_wdi_row("USA", "2022", 2.0)]),
    }
    calls: list[str] = []

    def fake_get_json(url, **kwargs):
        calls.append(url)
        page = int(url.split("&page=")[1])
        return pages[page]

    monkeypatch.setattr(pipeline, "_get_json", fake_get_json)
    monkeypatch.setattr(pipeline, "WB_WDI_INDICATORS", {"NY.GDP.MKTP.KD": "gdp_constant_usd"})

    rows = list(pipeline.wb_wdi())
    assert [r["year"] for r in rows] == [2023, 2022]
    assert all(r["indicator"] == "NY.GDP.MKTP.KD" for r in rows)
    assert len(calls) == 2  # stopped after the last page, didn't loop forever


def test_wb_wdi_normalises_row_shape(monkeypatch):
    """`date` becomes an int year; a missing date becomes None, not a crash."""
    monkeypatch.setattr(
        pipeline,
        "_get_json",
        lambda url, **kw: _wdi_page(1, 1, [_wdi_row("KEN", "1999", None), {"value": 3.0}]),
    )
    monkeypatch.setattr(pipeline, "WB_WDI_INDICATORS", {"SP.POP.TOTL": "population"})

    rows = list(pipeline.wb_wdi())
    assert rows[0] == {
        "indicator": "SP.POP.TOTL",
        "country_iso3": "KEN",
        "year": 1999,
        "value": None,
    }
    assert rows[1]["year"] is None and rows[1]["country_iso3"] is None


def test_wb_wdi_rejects_error_object_served_with_200(monkeypatch):
    """The World Bank answers a bad indicator code with a 200 and a message
    object, which `raise_for_status` can't catch.

    Asserted as `ResourceExtractionError` because that's what a caller actually
    sees: dlt wraps anything a resource generator raises, and the wrapper is
    what fails the load. The original message is chained through, hence `match`.
    """
    monkeypatch.setattr(
        pipeline,
        "_get_json",
        lambda url, **kw: {"message": [{"id": "120", "value": "Invalid value"}]},
    )
    monkeypatch.setattr(pipeline, "WB_WDI_INDICATORS", {"NOPE": "nope"})

    with pytest.raises(ResourceExtractionError, match="unexpected World Bank payload"):
        list(pipeline.wb_wdi())


# --------------------------------------------------------------------------- #
# eu_elec_prices — JSON-stat grid walking
# --------------------------------------------------------------------------- #


def _jsonstat(values: dict[str, float]) -> dict:
    """A 3-dimension cube shaped like Eurostat's: one filtered dimension in
    front, then geo (2) × time (3). Row-major, so time strides by 1 and geo
    by 3 — a payload with geo last would pass even with the strides reversed.
    """
    return {
        "id": ["unit", "geo", "time"],
        "size": [1, 2, 3],
        "dimension": {
            "unit": {"category": {"index": {"KWH": 0}}},
            "geo": {"category": {"index": {"EL": 0, "DE": 1}}},
            "time": {"category": {"index": {"2023-S1": 0, "2023-S2": 1, "2024-S1": 2}}},
        },
        "value": values,
    }


def test_eu_elec_prices_walks_the_grid(monkeypatch):
    # flat index = geo * 3 + time, so EL/2023-S2 is 1 and DE/2024-S1 is 5
    monkeypatch.setattr(
        pipeline,
        "_get_json",
        lambda url, **kw: _jsonstat({"1": 0.19, "5": 0.40}),
    )
    rows = list(pipeline.eu_elec_prices())
    assert rows == [
        {"geo": "EL", "period": "2023-S2", "year": 2023, "price_eur_kwh": 0.19},
        {"geo": "DE", "period": "2024-S1", "year": 2024, "price_eur_kwh": 0.40},
    ]


def test_eu_elec_prices_skips_absent_cells(monkeypatch):
    """The cube is sparse — most (geo, period) cells have no observation, and
    they must be dropped rather than yielded as nulls."""
    monkeypatch.setattr(pipeline, "_get_json", lambda url, **kw: _jsonstat({"0": 0.25}))
    rows = list(pipeline.eu_elec_prices())
    assert len(rows) == 1
    assert rows[0]["geo"] == "EL" and rows[0]["period"] == "2023-S1"
