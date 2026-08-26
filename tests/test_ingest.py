"""Unit tests for the ingest layer's fetching and parsing.

Every HTTP call here is mocked. What's under test is the code that has actually
broken in this repo before — the retry/raise path, the WDI pagination loop, and
the Eurostat JSON-stat stride arithmetic — none of which is worth a live API
call to exercise. The end-to-end path lives in `just test-pipeline`.
"""

from __future__ import annotations

import json
import re
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import requests
from dlt.extract.exceptions import ResourceExtractionError

from ingest import fixtures, pipeline
from modern_data_stack import workbook
from transform import pipeline_status


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
    return {
        "country": {"id": iso3[:2], "value": iso3},
        "countryiso3code": iso3,
        "date": date,
        "value": value,
    }


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
        "country_code": "KE",
        "country_iso3": "KEN",
        "year": 1999,
        "value": None,
    }
    assert rows[1]["year"] is None and rows[1]["country_iso3"] is None
    # the merge key comes from `country.id`, so a payload without it is a null
    # key — declared non-nullable in WDI_COLUMNS, so the load fails loudly
    assert rows[1]["country_code"] is None


def test_wb_wdi_merge_key_survives_the_aggregate_rows(monkeypatch):
    """The World Bank's aggregate series ("Arab World", "World") carry an empty
    `countryiso3code`, so keying the merge on it would collide five rows onto
    `(indicator, '', year)` and keep one at random."""
    aggregates = [
        {"country": {"id": "1A", "value": "Arab World"}, "countryiso3code": "", "date": "2020"},
        {"country": {"id": "WLD", "value": "World"}, "countryiso3code": "", "date": "2020"},
    ]
    monkeypatch.setattr(pipeline, "_get_json", lambda url, **kw: _wdi_page(1, 1, aggregates))
    monkeypatch.setattr(pipeline, "WB_WDI_INDICATORS", {"SP.POP.TOTL": "population"})

    keys = {tuple(row[col] for col in pipeline.WDI_PRIMARY_KEY) for row in pipeline.wb_wdi()}
    assert len(keys) == 2


def test_wdi_url_asks_for_the_whole_series_by_default():
    assert "date=" not in pipeline.wdi_url("SP.POP.TOTL")


def test_wdi_url_adds_the_date_window():
    url = pipeline.wdi_url("SP.POP.TOTL", 1, 2021)
    assert f"&date=2021:{datetime.now(UTC).year}" in url
    # the fixture route keys on the indicator code, so the window mustn't move it
    assert fixtures.path_for(url).name == "wb_wdi_SP.POP.TOTL.json"


def test_wdi_url_closes_the_window_when_given_an_end_year():
    """A partition asks for one year, so the window needs a right-hand end — the
    incremental path leaves it open at today."""
    url = pipeline.wdi_url("SP.POP.TOTL", 1, 1995, 1995)
    assert "&date=1995:1995" in url
    assert fixtures.path_for(url).name == "wb_wdi_SP.POP.TOTL.json"


def test_wdi_start_year_is_a_lookback_window_not_the_watermark():
    """Restatements are the whole reason for the window: asking for `> 2025`
    would never see the World Bank revise 2023."""
    assert pipeline.wdi_start_year(None) is None  # first load: everything
    assert pipeline.wdi_start_year(2025) == 2025 - pipeline.WDI_LOOKBACK_YEARS + 1


def _state(monkeypatch, state: dict) -> dict:
    """Stand in for dlt's per-resource state, which is only real inside a run."""
    monkeypatch.setattr(pipeline.dlt.current, "resource_state", lambda: state)
    return state


def _serve_wdi(monkeypatch, rows_by_indicator: dict[str, list[dict]]) -> list[str]:
    """Serve one page per indicator, recording the URLs asked for."""
    calls: list[str] = []

    def fake_get_json(url, **kwargs):
        calls.append(url)
        code = url.split("/indicator/")[1].split("?")[0]
        return _wdi_page(1, 1, rows_by_indicator[code])

    monkeypatch.setattr(pipeline, "_get_json", fake_get_json)
    monkeypatch.setattr(
        pipeline, "WB_WDI_INDICATORS", {code: code.lower() for code in rows_by_indicator}
    )
    return calls


def test_wb_wdi_first_load_fetches_everything_and_records_a_watermark(monkeypatch):
    state = _state(monkeypatch, {})
    calls = _serve_wdi(
        monkeypatch,
        {"SP.POP.TOTL": [_wdi_row("USA", "2024", 1.0), _wdi_row("USA", "2023", 2.0)]},
    )

    list(pipeline.wb_wdi())
    assert all("date=" not in url for url in calls)
    assert state[pipeline.WDI_WATERMARK_KEY] == {"SP.POP.TOTL": 2024}


def test_wb_wdi_incremental_load_asks_only_for_the_lookback_window(monkeypatch):
    """The point of the exercise: a run with a watermark re-fetches five years,
    not sixty-five."""
    state = _state(monkeypatch, {pipeline.WDI_WATERMARK_KEY: {"SP.POP.TOTL": 2025}})
    calls = _serve_wdi(monkeypatch, {"SP.POP.TOTL": [_wdi_row("USA", "2025", 1.0)]})

    list(pipeline.wb_wdi())
    assert all(f"&date={2025 - pipeline.WDI_LOOKBACK_YEARS + 1}:" in url for url in calls)
    # the window can't drag the watermark backwards
    assert state[pipeline.WDI_WATERMARK_KEY] == {"SP.POP.TOTL": 2025}


def test_wb_wdi_watermarks_are_per_indicator(monkeypatch):
    """A newly added indicator has no watermark, so it gets its whole series
    while the established ones stay on the window.

    The failure this prevents is silent: one watermark for the whole table would
    give the new column five years of history and no error anywhere.
    """
    _state(monkeypatch, {pipeline.WDI_WATERMARK_KEY: {"SP.POP.TOTL": 2025}})
    calls = _serve_wdi(
        monkeypatch,
        {
            "SP.POP.TOTL": [_wdi_row("USA", "2025", 1.0)],
            "NEW.CODE": [_wdi_row("USA", "1960", 2.0)],
        },
    )

    list(pipeline.wb_wdi())
    windowed = [url for url in calls if "date=" in url]
    assert len(windowed) == 1 and "SP.POP.TOTL" in windowed[0]


def test_wb_wdi_full_reload_env_var_ignores_the_watermark(monkeypatch):
    state = _state(monkeypatch, {pipeline.WDI_WATERMARK_KEY: {"SP.POP.TOTL": 2025}})
    monkeypatch.setenv("INGEST_WDI_FULL", "1")
    calls = _serve_wdi(
        monkeypatch,
        {"SP.POP.TOTL": [_wdi_row("USA", "1960", 1.0), _wdi_row("USA", "2025", 2.0)]},
    )

    list(pipeline.wb_wdi())
    assert all("date=" not in url for url in calls)  # the whole series again
    assert state[pipeline.WDI_WATERMARK_KEY] == {"SP.POP.TOTL": 2025}


# --------------------------------------------------------------------------- #
# wb_wdi — the partitioned (backfill) window
# --------------------------------------------------------------------------- #


def test_wb_wdi_partition_window_is_asked_for_verbatim(monkeypatch):
    """A partition key means "load exactly these years", for every indicator —
    including the ones that have a watermark, which the incremental path would
    have windowed differently."""
    _state(monkeypatch, {pipeline.WDI_WATERMARK_KEY: {"SP.POP.TOTL": 2025}})
    calls = _serve_wdi(
        monkeypatch,
        {
            "SP.POP.TOTL": [_wdi_row("USA", "1990", 1.0)],
            "NEW.CODE": [_wdi_row("USA", "1990", 2.0)],
        },
    )

    list(pipeline.wb_wdi((1990, 1995)))
    assert all("&date=1990:1995" in url for url in calls)
    assert len(calls) == 2  # one request per indicator, not one per year


def test_wb_wdi_backfill_does_not_move_the_watermark(monkeypatch):
    """The watermark means "everything up to here is loaded", which a backfill
    of one window can't claim.

    The failure it prevents: partitions 2020-2025 into an empty warehouse would
    leave a 2025 watermark, and the next incremental run would look back five
    years over sixty years of history that was never fetched.
    """
    state = _state(monkeypatch, {})
    _serve_wdi(monkeypatch, {"SP.POP.TOTL": [_wdi_row("USA", "2025", 1.0)]})

    list(pipeline.wb_wdi((2020, 2025)))
    assert state[pipeline.WDI_WATERMARK_KEY] == {}


def test_public_indicators_threads_the_window_to_the_resource(monkeypatch):
    """The window reaches the resource through the *source*, which is what lets
    the Dagster asset build a partitioned load with the same call the CLI makes
    for an incremental one."""
    _state(monkeypatch, {})
    calls = _serve_wdi(monkeypatch, {"SP.POP.TOTL": [_wdi_row("USA", "1975", 1.0)]})

    source = pipeline.public_indicators(wdi_years=(1975, 1975))
    list(source.resources["wb_wdi"])
    assert calls and all("&date=1975:1975" in url for url in calls)


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
# load_groups — the replace/merge split
# --------------------------------------------------------------------------- #


def test_load_groups_refreshes_only_the_replace_resources():
    """`refresh="drop_resources"` would drop `wb_wdi`'s table and watermark, so
    the merge resource has to load in its own call, without it."""
    groups = {tuple(names): kwargs for names, kwargs in pipeline.load_groups()}
    assert groups[pipeline.FULL_REFRESH_RESOURCES] == {"refresh": pipeline.REFRESH}
    assert groups[pipeline.INCREMENTAL_RESOURCES] == {}


def test_load_groups_covers_every_resource_in_the_source_exactly_once():
    """A resource missing from both tuples would silently stop being loaded.

    The two tuples are also what the two `@dlt_assets` blocks are built from,
    so this is what keeps the split from dropping a resource out of the asset
    graph as well.
    """
    listed = [name for names, _ in pipeline.load_groups() for name in names]
    assert sorted(listed) == sorted(r.name for r in pipeline.public_indicators().resources.values())
    assert len(listed) == len(set(listed))


def test_source_tables_names_every_resource_in_the_source_exactly_once():
    """`pipeline_status.SOURCE_TABLES` re-enumerates the seven dlt resources.

    `observability.build_sources` iterates only the names it is handed, so a
    resource missing here yields no row and the pipeline page under-reports
    while looking complete. That exact symptom has already happened once from a
    different cause — the Arrow path landing `retail_invoice_lines` with no
    `_dlt_load_id`, so the page showed six sources for seven — and this list is
    a second, unguarded route to the identical wrong page.

    Asserted rather than derived. Deriving would delete the list, but it would
    also make `transform/` import from `ingest/`, which no transform module
    does: `pipeline_status` is the one module that must run *after* dbt rather
    than beside ingestion, and coupling it to the ingest layer at runtime to
    avoid restating seven strings is the worse trade. It is also the shape every
    other list in this repo already uses — `load_groups`, `PARTITIONED_RESOURCES`
    and `TABLE_TO_ASSET_KEY` are all held to their source, none is derived from
    it.

    Lives here rather than in `tests/test_pipeline_status.py` because that
    module has an *autouse* fixture replacing `SOURCE_TABLES` with a one-name
    stub. A guard written there would assert against the stub and pass forever.
    """
    listed = list(pipeline_status.SOURCE_TABLES)
    assert sorted(listed) == sorted(r.name for r in pipeline.public_indicators().resources.values())
    assert len(listed) == len(set(listed))


def test_partitioned_resources_is_a_subset_of_the_incremental_ones():
    """The orchestration split is by *partitioning*, not by disposition, and the
    two questions are not the same one — `ecb_fx_rates` merges and is not
    partitioned.

    `orchestration/assets.py` derives its unpartitioned block as everything
    minus this tuple, so a name in here that isn't a real resource would quietly
    remove nothing and leave WDI's block empty.
    """
    assert set(pipeline.PARTITIONED_RESOURCES) <= set(pipeline.INCREMENTAL_RESOURCES)
    # Named explicitly, because `orchestration/assets.py` splits this tuple again
    # by partition *grain* — years for WDI and weather, months for retail — into
    # two `@dlt_assets` blocks, and Dagster forces one `partitions_def` per
    # block. A resource added here without a matching block would land in
    # neither and disappear from the graph, which no other assertion here would
    # catch. Two resources sharing the yearly grain is the case that makes the
    # distinction between this tuple and the *blocks* worth keeping: they are
    # one block, not two, and `full_refresh` would refuse to resolve if they
    # carried separate partitions definitions.
    assert set(pipeline.PARTITIONED_RESOURCES) == {
        "wb_wdi",
        "retail_invoice_lines",
        "om_weather_daily",
    }
    unpartitioned = [
        name
        for names, _ in pipeline.load_groups()
        for name in names
        if name not in pipeline.PARTITIONED_RESOURCES
    ]
    assert sorted(unpartitioned + list(pipeline.PARTITIONED_RESOURCES)) == sorted(
        r.name for r in pipeline.public_indicators().resources.values()
    )


STG_WDI = Path(__file__).resolve().parent.parent / "dbt" / "models" / "staging" / "stg_wdi.sql"

# `max(case when indicator = '<code>' then value end) as <column>`, the one line
# shape `stg_wdi.sql` uses for every indicator. Read off the SQL text and not the
# dbt manifest on purpose: this file runs in `just test`, which comes *before*
# `dbt deps && dbt parse` in ci.yml, and a guard that needs the manifest would
# skip itself exactly where a fresh clone needs it most.
WDI_PIVOT = re.compile(
    r"max\(\s*case\s+when\s+indicator\s*=\s*'([^']+)'\s+then\s+value\s+end\s*\)\s+as\s+(\w+)",
    re.IGNORECASE,
)


def test_the_wdi_pivot_maps_every_indicator_to_the_column_it_was_configured_for():
    """`WB_WDI_INDICATORS` and `stg_wdi.sql` restate the same mapping.

    The dict in `ingest/pipeline.py` already carries the column name
    (`"NY.GDP.MKTP.KD": "gdp_constant_usd"`), and `stg_wdi.sql` says it again as
    a `max(case ...)` branch. Eleven entries, written twice, and until this test
    nothing tied them — while CLAUDE.md documents "add indicators in two places"
    as the workflow, so the divergence is invited rather than accidental.

    Three ways they can drift and all three are silent. A configured indicator
    with no branch lands in `raw.wb_wdi` and never reaches a column. A branch
    with no indicator is a column of nulls. **Worst is a code against the wrong
    column**, because the columns it can plausibly be crossed with are the ones
    that look alike: swap `NY.GDP.MKTP.CD` for `.KD` and current-dollar GDP
    lands in `gdp_constant_usd`, which is what every intensity figure downstream
    divides by. All 14 of this model's data tests are `accepted_range`, and both
    series are non-negative USD, so every one passes — and per CLAUDE.md's GDP
    section that substitution flips the decarbonisation *sign* for 30 countries.

    WDI is the reason this list is worth guarding when others are not: it is a
    live source the World Bank revises, and adding an indicator is a routine
    edit. A frozen source cannot drift into any of these states.

    Every branch below has been seen to fire: deleting a pivot line, adding one
    for an unconfigured code, crossing `.CD` with `.KD`, and changing the pivot
    idiom so nothing matches. The whitespace tolerance is deliberate and was
    checked too — reflowing one branch across a newline still matches, so a
    `sqlfluff` reformat cannot silently empty this test.
    """
    pivot = dict(WDI_PIVOT.findall(STG_WDI.read_text()))
    configured = dict(pipeline.WB_WDI_INDICATORS)

    # Vacuity guard, the same reason `test_documented_counts.py` carries one: a
    # regex that stops matching passes by not looking, and this one reads a file
    # nothing else in the suite parses.
    assert len(pivot) >= 10, (
        f"WDI_PIVOT matched only {len(pivot)} branches in {STG_WDI.name}; "
        "the pattern or the model's formatting has drifted"
    )

    missing = sorted(set(configured) - set(pivot))
    assert not missing, (
        f"configured in WB_WDI_INDICATORS but not pivoted in {STG_WDI.name}: {missing} — "
        "they land in raw.wb_wdi and reach no column"
    )
    # Its own assertion, not folded into the one above: renaming a code produces
    # a gap *and* an orphan, the first assert wins, and the orphan branch is then
    # never measured. Same finding as the `RAW_DESCRIPTIONS` guard.
    orphaned = sorted(set(pivot) - set(configured))
    assert not orphaned, (
        f"pivoted in {STG_WDI.name} but not configured in WB_WDI_INDICATORS: {orphaned} — "
        "the column is all nulls"
    )
    # The dangerous one, and it is invisible to both checks above: the two
    # collections agree on *which* codes exist and disagree on where they go.
    crossed = {
        code: (configured[code], pivot[code])
        for code in configured
        if configured[code] != pivot[code]
    }
    assert not crossed, (
        "WB_WDI_INDICATORS and stg_wdi.sql disagree about which column an "
        f"indicator feeds (code: configured -> pivoted): {crossed}"
    )


def test_load_groups_drops_groups_the_selection_empties():
    """Dagster materialising one raw asset must not run a load with no
    resources in it."""
    assert pipeline.load_groups(["wb_wdi"]) == [(["wb_wdi"], {})]
    assert pipeline.load_groups(["owid_co2"]) == [(["owid_co2"], {"refresh": pipeline.REFRESH})]
    assert pipeline.load_groups([]) == []


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


# --------------------------------------------------------------------------- #
# ecb_fx_rates — the daily grain, and its watermark
# --------------------------------------------------------------------------- #


def _fx_payload(rates: dict[str, dict[str, float]]) -> dict:
    return {"amount": 1.0, "base": "EUR", "rates": rates}


def _serve_fx(monkeypatch, payload: dict) -> list[str]:
    """Serve one Frankfurter response, recording the URL asked for."""
    calls: list[str] = []

    def fake_get_json(url, **kwargs):
        calls.append(url)
        return payload

    monkeypatch.setattr(pipeline, "_get_json", fake_get_json)
    return calls


def test_fx_start_date_is_the_series_start_until_a_watermark_exists():
    assert pipeline.fx_start_date(None) == pipeline.FX_FIRST_DATE


def test_fx_start_date_looks_back_from_the_watermark():
    """Ten days, not one: a corrected fixing inside the window has to be able to
    reach the warehouse, and the merge key makes re-asking free."""
    assert pipeline.fx_start_date("2026-08-07") == "2026-07-29"


def test_fx_start_date_cannot_ask_for_dates_before_the_euro():
    """A watermark near the start of the series would otherwise produce a range
    the API answers with an empty `rates` object."""
    assert pipeline.fx_start_date("1999-01-05") == pipeline.FX_FIRST_DATE


def test_ecb_fx_rates_unpivots_the_wide_payload(monkeypatch):
    """The API is `{date: {currency: rate}}` and the merge key is
    (rate_date, quote_currency), so the resource has to land it long. A wide
    landing table would need a new column every time the ECB lists a currency,
    which is exactly what dlt's widen-only schema handles worst.
    """
    _state(monkeypatch, {})
    _serve_fx(
        monkeypatch,
        _fx_payload({"2024-01-02": {"USD": 1.0956, "JPY": 155.68}}),
    )

    # dlt flattens a yielded list into individual items, so this is rows not batches.
    rows = list(pipeline.ecb_fx_rates())
    assert rows == [
        {
            "rate_date": "2024-01-02",
            "base_currency": "EUR",
            "quote_currency": "USD",
            "rate": 1.0956,
        },
        {
            "rate_date": "2024-01-02",
            "base_currency": "EUR",
            "quote_currency": "JPY",
            "rate": 155.68,
        },
    ]


def test_ecb_fx_rates_advances_the_watermark_to_the_newest_date(monkeypatch):
    """One watermark for the table, not one per currency — every currency comes
    back in the same request, so a newly listed one is already covered."""
    state = _state(monkeypatch, {})
    _serve_fx(
        monkeypatch,
        _fx_payload(
            {
                "2024-01-02": {"USD": 1.0956},
                "2024-01-10": {"USD": 1.0946},
                "2024-01-05": {"USD": 1.0921},
            }
        ),
    )

    list(pipeline.ecb_fx_rates())
    assert state[pipeline.FX_WATERMARK_KEY] == "2024-01-10"


def test_ecb_fx_rates_leaves_the_watermark_alone_on_an_empty_response(monkeypatch):
    """A window falling entirely on a weekend legitimately returns no rates.
    Advancing on that would advance to a date that was never loaded.
    """
    state = _state(monkeypatch, {pipeline.FX_WATERMARK_KEY: "2024-01-10"})
    _serve_fx(monkeypatch, _fx_payload({}))

    assert list(pipeline.ecb_fx_rates()) == []
    assert state[pipeline.FX_WATERMARK_KEY] == "2024-01-10"


def test_ecb_fx_rates_asks_for_the_lookback_window_when_it_has_a_watermark(monkeypatch):
    _state(monkeypatch, {pipeline.FX_WATERMARK_KEY: "2026-08-07"})
    calls = _serve_fx(monkeypatch, _fx_payload({"2026-08-07": {"USD": 1.1535}}))

    list(pipeline.ecb_fx_rates())
    assert len(calls) == 1
    assert "/v1/2026-07-29.." in calls[0]
    assert "base=EUR" in calls[0]


# --------------------------------------------------------------------------- #
# Retail order lines (UCI Online Retail II)
# --------------------------------------------------------------------------- #


@pytest.fixture
def retail_con(monkeypatch, tmp_path):
    """A connection over the *fixture* workbook, cached into a temp directory.

    `INGEST_CACHE_DIR` is redirected for the same reason `just test-pipeline`
    redirects `WAREHOUSE_PATH`: without it these tests would unpack into
    `data/cache/`, and the fixture slice and the real 45 MB workbook share a
    filename.
    """
    monkeypatch.setenv(fixtures.ENV_VAR, "1")
    monkeypatch.setenv("INGEST_CACHE_DIR", str(tmp_path))
    con = workbook.connect()
    con.execute(
        f"create or replace view sheets as {workbook.sheets_sql(pipeline.retail_workbook())}"
    )
    return con


def test_retail_fixture_and_live_caches_never_share_a_path(monkeypatch, tmp_path):
    """The one that would poison the real warehouse.

    Both workbooks are named `online_retail_II.xlsx`. If a fixture run unpacked
    to the shared cache path, the next *live* run would find it, skip the
    download and load the 41k-row slice into the real warehouse — green, silent
    and wrong. Same failure the `_fixtures` pipeline-name suffix prevents for
    dlt state.
    """
    monkeypatch.setenv("INGEST_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv(fixtures.ENV_VAR, "1")
    fixture_path = pipeline.retail_workbook()
    monkeypatch.setenv(fixtures.ENV_VAR, "0")
    live_dir = Path(pipeline.cache_dir()) / "live"
    assert fixture_path.parent != live_dir
    assert not (live_dir / pipeline.RETAIL_WORKBOOK_NAME).exists()


def test_retail_cache_notices_a_re_recorded_archive(monkeypatch, tmp_path):
    """The same poisoning one level in: a stale *extract* under a fresh archive.

    The cache used to be keyed on the directory alone — if the workbook was
    there, it was returned — so it could not see that the zip underneath had
    changed. `just record-fixtures` rewrites that zip, and the next
    `INGEST_FIXTURES=1` run would then load the *previous* slice: every fixture
    test green against data the repo no longer contained, which is the failure
    mode that makes a re-recording look like a no-op.
    """
    monkeypatch.setenv(fixtures.ENV_VAR, "1")
    monkeypatch.setenv("INGEST_CACHE_DIR", str(tmp_path / "cache"))

    archive = tmp_path / "retail.zip"
    monkeypatch.setattr(pipeline.fixtures, "path_for", lambda url: archive)

    def record(payload: bytes) -> None:
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(pipeline.RETAIL_WORKBOOK_NAME, payload)

    record(b"the first recording")
    first = pipeline.retail_workbook()
    assert first.read_bytes() == b"the first recording"
    # Cached: same archive, same path, no second extraction.
    assert pipeline.retail_workbook() == first

    record(b"a re-recorded slice")
    second = pipeline.retail_workbook()
    assert second != first
    assert second.read_bytes() == b"a re-recorded slice"


def test_retail_yields_every_row_the_workbook_holds(retail_con):
    """The silent truncation this cost once already.

    `DuckDBPyRelation.arrow()` returns a streaming reader whose default batch is
    1,000,000 rows, and treating it as a table lands the first batch and no
    warning — the live load quietly stored exactly 1,000,000 of 1,067,371 rows
    until the round number gave it away. Pinned against the workbook's own count
    so a batch-size change can't bring it back.
    """
    expected = retail_con.sql("select count(*) from sheets").fetchone()[0]
    batches = list(pipeline.retail_invoice_lines())
    assert len(batches) >= 1
    assert sum(b.num_rows for b in batches) == expected


def test_retail_line_number_is_stable_across_reads(retail_con):
    """The merge key rests on file order, so file order has to be deterministic.

    34,337 rows in the full workbook are exact duplicates of another row — same
    invoice, product, quantity, price, timestamp — so nothing in the content can
    tell them apart. `workbook.connect()` pins `preserve_insertion_order`; this
    is the assertion that makes that setting load-bearing rather than incidental.

    Sorted before comparing, because the claim is about the *assignment* and not
    about output order: `partition by invoice` lets DuckDB return the rows in
    whatever order it likes, and it does. What has to hold is that a given
    (invoice, line_number) names the same line every time.
    """
    read = lambda: retail_con.sql(  # noqa: E731
        f"""select invoice, line_number, stock_code, quantity
            from ({pipeline.retail_sql()}) order by invoice, line_number"""
    ).fetchall()
    assert read() == read()


def test_retail_month_filter_is_the_partition(retail_con):
    """A partition loads its window and nothing else, and the windows tile."""
    all_rows = retail_con.sql(f"select count(*) from ({pipeline.retail_sql()})").fetchone()[0]
    window = pipeline.retail_sql(("2010-01", "2010-03"))
    months = retail_con.sql(f"select distinct invoice_month from ({window}) order by 1").fetchall()
    assert [m[0] for m in months] == ["2010-01", "2010-02", "2010-03"]

    per_month = sum(
        retail_con.sql(f"select count(*) from ({pipeline.retail_sql((m, m))})").fetchone()[0]
        for m in ("2010-01", "2010-02", "2010-03")
    )
    windowed = retail_con.sql(f"select count(*) from ({window})").fetchone()[0]
    assert per_month == windowed
    assert windowed < all_rows


def test_retail_month_filter_never_splits_an_invoice(retail_con):
    """Line numbers are assigned per invoice, so a filter that cut one in half
    would renumber the surviving lines and change the merge key. Invoices don't
    straddle month boundaries — this is what says so."""
    straddling = retail_con.sql(
        f"""select count(*) from (
                select invoice from ({pipeline.retail_sql()})
                group by 1 having count(distinct invoice_month) > 1)"""
    ).fetchone()[0]
    assert straddling == 0


def test_retail_partition_bounds_match_the_data(retail_con):
    """`RETAIL_FIRST_MONTH`/`RETAIL_LAST_MONTH` define the Dagster partitions and
    are therefore hardcoded — they have to be known before anything is loaded. A
    re-recorded fixture that widened the window would otherwise leave months with
    no partition to land in."""
    lo, hi = retail_con.sql(
        f"select min(invoice_month), max(invoice_month) from ({pipeline.retail_sql()})"
    ).fetchone()
    assert lo >= pipeline.RETAIL_FIRST_MONTH
    assert hi <= pipeline.RETAIL_LAST_MONTH


def test_retail_lands_the_source_verbatim(retail_con):
    """Staging owns the taxonomy; the landing table owns nothing.

    Every one of these would be tempting to "fix" in a cast — and each one is a
    distinction a downstream model needs to see.
    """
    row = retail_con.sql(
        f"""select
                sum(starts_with(invoice, 'C')::int) as cancellations,
                sum(starts_with(invoice, 'A')::int) as adjustments,
                sum((quantity < 0)::int) as negative_quantity,
                sum((unit_price < 0)::int) as negative_price,
                sum((customer_id is null or customer_id = '')::int) as no_customer,
                count(distinct case when upper(stock_code) <> stock_code then stock_code end)
                    as lowercase_codes
            from ({pipeline.retail_sql()})"""
    ).fetchone()
    cancellations, adjustments, negative_qty, negative_price, no_customer, lowercase = row
    assert cancellations > 0
    assert adjustments == 6, "all six bad-debt adjustments must survive the fixture trim"
    assert negative_qty > cancellations, "write-offs are negative too, and are not cancellations"
    assert negative_price > 0
    assert no_customer > 0
    assert lowercase > 0, "case collisions must reach staging, which is what upper() is for"


# --------------------------------------------------------------------------- #
# Open-Meteo capital-city weather
# --------------------------------------------------------------------------- #


def _wb_country_row(iso3: str, iso2: str, latitude: str, longitude: str) -> dict:
    """One World Bank /country row, in the shape `weather_locations` reads."""
    return {
        "id": iso3,
        "iso2Code": iso2,
        "latitude": latitude,
        "longitude": longitude,
        "region": {"value": "Europe & Central Asia"},
    }


def _wb_country_payload(rows: list[dict]) -> list:
    return [{"page": 1, "pages": 1, "per_page": len(rows), "total": len(rows)}, rows]


def test_weather_call_units_matches_open_meteos_published_formula():
    """`(variables / 10) * (days / 14) * locations`, which is the number every
    pacing decision in this layer is made against.

    The second assertion is the measured one: a single 86-year three-variable
    request costs ~673 units against a 600-a-minute budget, which is why one of
    them is served and the next is refused. If this arithmetic drifts, the
    limiter is pacing against a fiction and the only symptom is a 429.
    """
    assert pipeline.weather_call_units(1, 14, variables=10) == pytest.approx(1.0)
    assert pipeline.weather_call_units(1, 31_412, variables=3) == pytest.approx(673.1, abs=0.1)
    # The default is the configured variable list, not a hardcoded count.
    assert pipeline.weather_call_units(41, 365) == pytest.approx(
        len(pipeline.WEATHER_DAILY_VARIABLES) / 10 * (365 / 14) * 41
    )


def test_weather_locations_are_sorted_and_scoped(monkeypatch):
    """Sorted because the response is matched to the request **by position**, so
    the order has to be a property of the code rather than of the API's row
    order — which is alphabetical by name, not by ISO3."""
    payload = _wb_country_payload(
        [
            _wb_country_row("POL", "PL", "52.26", "21.02"),
            _wb_country_row("DEU", "DE", "52.5235", "13.4115"),
            _wb_country_row("USA", "US", "38.8895", "-77.032"),  # out of scope
        ]
    )
    monkeypatch.setattr(pipeline, "_get_json", lambda url, **kw: payload)
    monkeypatch.setattr(pipeline, "WEATHER_COUNTRIES", ("DEU", "POL"))

    assert pipeline.weather_locations() == [
        ("DEU", 52.5235, 13.4115),
        ("POL", 52.26, 21.02),
    ]


def test_weather_locations_fails_closed_when_a_capital_has_no_coordinates(monkeypatch):
    """The dangerous direction is dropping the country and carrying on.

    The World Bank sends `''` for territories with no capital, and a shorter
    location list produces a shorter response — which is then zipped against the
    full list, handing every country after the gap its neighbour's weather. No
    key in the payload can repair that, so it has to raise here.
    """
    payload = _wb_country_payload(
        [
            _wb_country_row("DEU", "DE", "52.5235", "13.4115"),
            _wb_country_row("XKX", "XK", "", ""),
        ]
    )
    monkeypatch.setattr(pipeline, "_get_json", lambda url, **kw: payload)
    monkeypatch.setattr(pipeline, "WEATHER_COUNTRIES", ("DEU", "XKX"))

    with pytest.raises(RuntimeError, match="no capital coordinates for XKX"):
        pipeline.weather_locations()


def test_weather_url_carries_every_location_in_one_request():
    locations = [("DEU", 52.5235, 13.4115), ("POL", 52.26, 21.02)]
    url = pipeline.weather_url(locations, "2022-01-01", "2022-12-31")
    assert "latitude=52.5235,52.2600" in url
    assert "longitude=13.4115,21.0200" in url
    assert "start_date=2022-01-01&end_date=2022-12-31" in url
    for variable in pipeline.WEATHER_DAILY_VARIABLES:
        assert variable in url
    # Four decimals, so the URL is byte-stable for a location set and a recorded
    # fixture stays reproducible. ERA5's grid is 0.25 degrees, so the precision
    # cannot change which cell answers.
    assert "52.523500" not in url


def test_weather_end_date_stops_short_of_the_archives_edge():
    """Asking past the archive's last day is a 400, not an empty response, so the
    lag is a correctness requirement rather than politeness."""
    end = pipeline.weather_end_date(today=date(2026, 8, 27))
    assert end == "2026-08-24"
    assert pipeline.WEATHER_END_LAG_DAYS >= 2, (
        "the boundary was measured at exactly T-1, so T-1 itself fails on "
        "whichever side of the server's rollover a run lands"
    )


def test_weather_start_date_uses_the_seed_floor_then_the_lookback():
    assert pipeline.weather_start_date(None) == f"{pipeline.WEATHER_FIRST_YEAR}-01-01"
    # 90-day lookback, inclusive of the watermark day itself.
    assert pipeline.weather_start_date("2026-08-20") == "2026-05-23"
    # Clamped: a watermark near the floor must not ask for years the seed skipped.
    assert (
        pipeline.weather_start_date(f"{pipeline.WEATHER_FIRST_YEAR}-01-05")
        == f"{pipeline.WEATHER_FIRST_YEAR}-01-01"
    )


def test_weather_lookback_outlives_the_era5t_revision_window():
    """ERA5T is preliminary and superseded by final ERA5 two to three months
    later. Rows outside this window are never refetched — they are carried
    forward between releases — so a short lookback freezes preliminary numbers
    permanently rather than merely delaying a correction."""
    assert pipeline.WEATHER_LOOKBACK_DAYS >= 60


def test_weather_windows_are_calendar_years_clipped_at_both_ends():
    windows = pipeline.weather_windows(years=(2020, 2022), watermark=None, today=date(2026, 8, 27))
    assert windows == [
        ("2020-01-01", "2020-12-31"),
        ("2021-01-01", "2021-12-31"),
        ("2022-01-01", "2022-12-31"),
    ]


def test_weather_windows_never_ask_past_the_archive():
    """A backfill range reaching into the future is clipped, not refused — the
    current year is a legitimate partition and it is simply not finished."""
    windows = pipeline.weather_windows(years=(2026, 2030), watermark=None, today=date(2026, 8, 27))
    assert windows == [("2026-01-01", "2026-08-24")]


def test_weather_windows_start_from_the_watermarks_lookback():
    windows = pipeline.weather_windows(watermark="2026-08-20", today=date(2026, 8, 27))
    assert windows == [("2026-05-23", "2026-08-24")]


def test_weather_windows_are_empty_when_the_range_is_entirely_in_the_future():
    """Nothing to ask for is a normal outcome, and asking anyway is a 400 rather
    than an empty response — so the empty list has to be produced here.

    Note what does *not* reach this branch: a watermark ahead of the archive's
    edge, which happens on any run made within `WEATHER_END_LAG_DAYS` of the last
    one. That still yields a lookback window overlapping the archive, and
    re-asking for days already held is free because the resource merges.
    """
    assert pipeline.weather_windows(years=(2030, 2031), today=date(2026, 8, 27)) == []
    assert pipeline.weather_windows(watermark="2026-09-30", today=date(2026, 8, 27)) == [
        ("2026-07-03", "2026-08-24")
    ]


def test_weather_windows_chunk_the_seed_into_affordable_requests():
    """The structural assertion: no single window may cost more than the hourly
    budget, or the seed cannot complete however patiently it is paced."""
    windows = pipeline.weather_windows(watermark=None, today=date(2026, 8, 27))
    hourly = {window: budget for window, budget in pipeline.WEATHER_RATE_LIMITS}[3600.0]
    for start, end in windows:
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
        cost = pipeline.weather_call_units(len(pipeline.WEATHER_COUNTRIES), days)
        assert cost <= hourly, f"{start}..{end} costs {cost:,.0f} units against {hourly:,.0f}/hour"


def _weather_entry(days: list[str], mean: list[float], *, latitude=52.5, longitude=13.4) -> dict:
    return {
        "latitude": latitude,
        "longitude": longitude,
        "elevation": 38.0,
        "daily": {
            "time": days,
            "temperature_2m_mean": mean,
            "temperature_2m_max": [v + 2 for v in mean],
            "temperature_2m_min": [v - 2 for v in mean],
            "precipitation_sum": [0.0] * len(days),
            "wind_speed_10m_max": [10.0] * len(days),
            "shortwave_radiation_sum": [1.0] * len(days),
        },
    }


def test_weather_rows_match_the_response_to_the_request_by_position():
    """The gotcha this whole resource is shaped around.

    A multi-location response is a JSON array whose entries carry a
    `location_id` — except the first, which has none at all. So index is the only
    key, and a fixture or a live response arriving in a different order would
    silently give every country its neighbour's weather with no error anywhere.
    """
    locations = [("DEU", 52.5235, 13.4115), ("POL", 52.26, 21.02)]
    payload = [
        _weather_entry(["2022-01-01"], [10.8]),
        # As the API sends it: `location_id` present on the second, absent above.
        {**_weather_entry(["2022-01-01"], [-1.5], latitude=52.3, longitude=21.0), "location_id": 1},
    ]
    rows = list(pipeline._weather_rows(payload, locations))
    assert [(r["country_iso3"], r["temperature_2m_mean"]) for r in rows] == [
        ("DEU", 10.8),
        ("POL", -1.5),
    ]
    # The grid cell the API snapped to, not the capital's own coordinates.
    assert rows[0]["grid_latitude"] == 52.5 and rows[0]["grid_longitude"] == 13.4


def test_weather_rows_refuse_a_response_of_the_wrong_length():
    """Positional matching means a short response is unrecoverable, so it must
    stop the load rather than mislabel the rows it can see."""
    with pytest.raises(RuntimeError, match="matched by position"):
        list(
            pipeline._weather_rows(
                [_weather_entry(["2022-01-01"], [1.0])], [("DEU", 1.0, 2.0), ("POL", 3.0, 4.0)]
            )
        )


def test_weather_rows_accept_the_single_location_object_shape():
    """One location comes back as a bare object rather than a one-element array —
    a shape the resource never asks for today, and would meet the moment the
    scope was narrowed to one country."""
    rows = list(pipeline._weather_rows(_weather_entry(["2022-01-01"], [3.0]), [("DEU", 1.0, 2.0)]))
    assert len(rows) == 1 and rows[0]["country_iso3"] == "DEU"


def test_weather_rows_cover_every_declared_column():
    """The landing schema is declared, not inferred, so a variable added to
    `WEATHER_DAILY_VARIABLES` and not to `WEATHER_COLUMNS` would be dropped by
    dlt without a word."""
    rows = list(pipeline._weather_rows(_weather_entry(["2022-01-01"], [3.0]), [("DEU", 1.0, 2.0)]))
    assert set(rows[0]) == set(pipeline.WEATHER_COLUMNS)


def test_weather_retry_reads_the_window_off_the_429_message():
    """The response carries no `Retry-After`, only a sentence naming the window,
    and the three windows want waits three orders of magnitude apart."""
    assert pipeline.weather_retry_after("Minutely API request limit exceeded.") == 65.0
    assert pipeline.weather_retry_after("Hourly API request limit exceeded.") == 660.0


def test_a_spent_daily_budget_raises_rather_than_sleeping_through_a_day():
    """Waiting out the daily window inside a run is not a backoff — it is a hang
    that looks exactly like a crashed process for twenty-four hours."""
    with pytest.raises(RuntimeError, match="daily budget is spent"):
        pipeline.weather_retry_after("Daily API request limit exceeded.")


# Eurostat's `geo` codes are ISO2 except for two, remapped in
# `stg_eu_electricity_prices_semiannual.sql`. Restated here rather than parsed
# out of the SQL because it is two entries and the model states the same pair in
# a `case` expression the guard below would have to reimplement to read.
EUROSTAT_GEO_TO_ISO2 = {"EL": "GR", "UK": "GB"}


def _eurostat_price_countries() -> set[str]:
    """The ISO3 codes Eurostat publishes an electricity price for.

    Derived from the two recorded fixtures rather than from the warehouse, so
    this runs in `just test` — which has no database and, in a fresh clone, no
    `dbt/target/manifest.json` either. Same reasoning as the WDI pivot guard
    parsing `stg_wdi.sql` instead of the manifest.
    """
    cube = json.loads(fixtures.path_for(pipeline.EU_ELEC_PRICES_API).read_text())
    geos = {geo for geo in cube["dimension"]["geo"]["category"]["index"] if len(geo) == 2}
    iso2 = {EUROSTAT_GEO_TO_ISO2.get(geo, geo) for geo in geos}

    payload = json.loads(fixtures.path_for(pipeline.WB_COUNTRY_API).read_text())
    by_iso2 = {
        row["iso2Code"]: row["id"]
        for row in payload[1]
        if (row.get("region") or {}).get("value") != "Aggregates"
    }
    # The inner join is what drops `EA` — two letters, so `len(geo) == 2` keeps
    # it, and no country carries that ISO2. Exactly as the staging model behaves.
    return {by_iso2[code] for code in iso2 if code in by_iso2}


def test_weather_countries_are_exactly_the_ones_eurostat_prices():
    """`WEATHER_COUNTRIES` is the scope decision written down, and the only thing
    that makes it defensible is that it matches the data it exists to join to.

    Held to the source rather than to the comment beside it, for the same reason
    `SOURCE_TABLES` and `RAW_DESCRIPTIONS` are: the list costs API budget to be
    wrong in either direction. A country Eurostat starts publishing gets no
    weather and the mart column is quietly null for it; a country dropped from
    the price series keeps costing units forever for a join that no longer
    happens. Neither shows up as a failure anywhere else.
    """
    priced = _eurostat_price_countries()
    scoped = set(pipeline.WEATHER_COUNTRIES)

    assert scoped - priced == set(), (
        f"WEATHER_COUNTRIES fetches weather for {sorted(scoped - priced)}, which Eurostat "
        "publishes no electricity price for — budget spent on a join that cannot happen"
    )
    assert priced - scoped == set(), (
        f"Eurostat prices {sorted(priced - scoped)} and no weather is fetched for them — "
        "the mart column will be null with nothing saying why"
    )


def test_weather_countries_carry_no_duplicates():
    """A repeated code would send the same coordinates twice and shift every
    location after it against the response, which is matched by position."""
    assert len(pipeline.WEATHER_COUNTRIES) == len(set(pipeline.WEATHER_COUNTRIES))
