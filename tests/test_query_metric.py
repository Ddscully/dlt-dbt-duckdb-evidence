"""`query_metric`: the real metrics, compiled by MetricFlow, over a hand-built warehouse.

The metrics come from the real `semantic_manifest.json`, so these tests check
the yml's definitions and `agent/metrics.py` together. What they hold:

* the figures by year, worked by hand, and an `all` row of `customers` that is
  the distinct count, not the sum of the rows: MetricFlow does not roll distinct
  counts up, and one customer buying in two years is one customer;
* the partial-period note, which the loop appends verbatim;
* the refusals, which go back to the model as its next prompt;
* rounding, because float sums differ between runs in the last digits.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from retail_fact import cancel, fee, sale, warehouse

from modern_data_stack.paths import dbt_manifest_path, dbt_semantic_manifest_path

# ci.yml runs pytest before `dbt parse`, so the manifests are missing there; it
# re-runs this file after the parse, which `tests/test_workflows.py` enforces.
manifest_path = dbt_manifest_path()
semantic_manifest_path = dbt_semantic_manifest_path()
pytestmark = pytest.mark.skipif(
    not (Path(manifest_path).exists() and Path(semantic_manifest_path).exists()),
    reason="needs dbt/target/manifest.json — run `just dbt-deps` and `dbt parse` first",
)

# c1 buys in both years and from two countries; c2 in 2021 only. Amounts are
# exact in binary, so every figure below is exact.
LINES = [
    sale("2021-01-01", "A", 10, 10.0, invoice="I1", customer="c1", country="GBR"),
    sale("2021-06-01", "B", 2, 2.5, invoice="I2", customer="c2", country="FRA"),
    cancel("2021-06-02", "A", -1, -1.0, invoice="C1", customer="c1", country="GBR"),
    sale("2022-03-01", "A", 4, 4.0, invoice="I3", customer="c1", country="USA"),
    sale("2022-03-01", "B", 1, 0.5, invoice="I3", customer="c1", country="USA"),
    sale("2022-12-31", "B", 1, 0.5, invoice="I4", country="GBR"),  # nobody signed in
    fee("2022-12-31", 7.0),  # not revenue
]


@pytest.fixture(scope="module")
def catalog():
    from agent.catalog import load_catalog

    return load_catalog()


@pytest.fixture(scope="module")
def layer(catalog):
    return catalog.layer


def _query(layer, lines=LINES, *args, **kwargs):
    from agent.metrics import query_metric

    return query_metric(warehouse(lines), layer, *args, **kwargs)


def test_figures_by_year_and_the_all_row(layer):
    table = _query(
        layer, LINES, ["net_revenue_gbp", "orders", "customers", "return_rate_pct"], ["year"]
    )
    assert table.rows == (
        (("2021",), (11.5, 2, 2, 8.0)),  # 10 + 2.5 - 1; returns 1 of 12.5 gross
        (("2022",), (5.0, 2, 1, 0.0)),
    )
    # Customers are not 2 + 1: c1 bought in both years.
    assert table.total == (16.5, 4, 2, pytest.approx(100 / 17.5))
    assert table.overcounted == ("customers",)


EVERY_METRIC = [
    "net_revenue_gbp",
    "gross_revenue_gbp",
    "returns_gbp",
    "return_rate_pct",
    "orders",
    "customers",
    "units_sold",
    "average_order_value_gbp",
    "average_selling_price_gbp",
]


def test_each_column_holds_the_metric_it_is_named_for(layer):
    # MetricFlow returns the metrics in its own order, which differs from this
    # one, so every figure is worked by hand: 2021 sells 12 units for 12.5 and
    # takes 1.0 back over 2 invoices; 2022 sells 6 units for 5.0 over 2.
    table = _query(layer, LINES, EVERY_METRIC, ["year"])
    assert table.metrics == tuple(EVERY_METRIC)
    assert table.rows == (
        (("2021",), (11.5, 12.5, -1.0, 8.0, 2, 2, 12, 5.75, pytest.approx(12.5 / 12))),
        (("2022",), (5.0, 5.0, 0.0, 0.0, 2, 1, 6, 2.5, pytest.approx(5 / 6))),
    )
    assert table.total == (
        *(16.5, 17.5, -1.0, pytest.approx(100 / 17.5)),
        *(4, 2, 18, 4.125, pytest.approx(17.5 / 18)),
    )
    assert table.overcounted == ("customers",)


def test_a_place_may_be_named_before_the_time_grain(layer):
    # MetricFlow puts the time grain first whatever the request says.
    table = _query(layer, LINES, ["net_revenue_gbp"], ["country", "year"])
    assert table.rows == (
        (("France", "2021"), (2.5,)),
        (("United Kingdom", "2021"), (9.0,)),
        (("United Kingdom", "2022"), (0.5,)),
        (("United States", "2022"), (4.5,)),
    )


def test_the_all_row_says_which_rows_overcount(layer):
    from agent.metrics import render

    text = render(_query(layer, LINES, ["orders", "customers"], ["country"]))
    assert "customers is a distinct count" in text
    assert "orders is a distinct count" not in text  # an invoice has one country


def test_a_filter_on_region(layer):
    table = _query(layer, LINES, ["net_revenue_gbp"], (), where={"region": "North America"})
    assert table.rows == (((), (4.5,)),)


def test_a_place_with_sales_and_no_returns_answers_zero(layer):
    # France bought (I2) and returned nothing. The values a filter accepts come
    # from every metric of the table, not from returns_gbp's own rows.
    table = _query(layer, LINES, ["returns_gbp"], (), where={"country": "France"})
    assert table.rows == (((), (0.0,)),)


def test_top_ranks_the_groups_and_judges_the_overcount_on_all_of_them(layer):
    from agent.metrics import render

    table = _query(layer, LINES, ["net_revenue_gbp", "customers"], ["country"], top=2)
    assert table.rows == (
        (("United Kingdom",), (9.5, 1)),  # 10 - 1 + 0.5; I4 has no customer
        (("United States",), (4.5, 1)),
    )
    assert table.ranked == (2, 3)
    # The two rows kept hold 2 customers, the total; all three hold 3. The
    # overcount is judged before ranking, or it would be missed.
    assert table.total == (16.5, 2)
    assert table.overcounted == ("customers",)
    assert "top 2 of 3" in render(table).splitlines()[0]


def test_a_period_covered_in_part_is_named(layer):
    from agent.metrics import coverage_note

    # The data runs 2021-01-01 to 2022-12-31: every year is whole.
    assert coverage_note(_query(layer, LINES, ["net_revenue_gbp"], ["year"])) is None

    lines = [*LINES[1:-2], sale("2022-11-30", "A", 1, 1.0)]  # 2021-06-01 to 2022-11-30
    by_year = coverage_note(_query(layer, lines, ["net_revenue_gbp"], ["year"]))
    assert by_year == (
        "Partly covered: 2021 (1 Jun to 31 Dec only), 2022 (1 Jan to 30 Nov only). "
        "To compare periods, use explain_change, which aligns them."
    )
    by_quarter = _query(layer, lines, ["net_revenue_gbp"], ["quarter"], years=(2022, 2022))
    assert by_quarter.partial == ("2022-Q4 (1 Oct to 30 Nov only)",)
    # Not grouped by time, the years asked for are the periods.
    assert _query(layer, lines, ["orders"], (), years=(2022, 2022)).partial == (
        "2022 (1 Jan to 30 Nov only)",
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"metrics": ["revenue"]}, "the metrics are"),
        ({"metrics": ["customers"], "group_by": ["customer_id"]}, "choose from year"),
        ({"metrics": ["orders"], "group_by": ["year", "month"]}, "one time grain"),
        # Each choice quoted: a name can hold a comma ("Hong Kong SAR, China").
        (
            {"metrics": ["orders"], "where": {"region": "Europe"}},
            'the values are "Europe & Central Asia", "North America"',
        ),
        ({"metrics": ["orders"], "years": (2020, 2021)}, "the data covers"),
        # top ranks places within one period, never periods.
        ({"metrics": ["orders"], "group_by": ["year"], "top": 2}, "top ranks"),
        ({"metrics": ["orders"], "top": 2}, "top ranks"),
    ],
)
def test_a_refusal_names_the_choices(layer, kwargs, message):
    with pytest.raises(ValueError, match=message):
        _query(layer, LINES, **kwargs)


def test_money_is_printed_to_the_penny(layer):
    from agent.metrics import render

    lines = [sale("2021-01-01", "A", 1, 0.1), sale("2021-12-31", "A", 1, 0.2)]
    table = _query(layer, lines, ["net_revenue_gbp"])
    assert table.rows[0][1][0] != 0.3  # the float sum really is off
    assert render(table).splitlines()[-1].strip() == "0.30"


def test_the_loop_offers_every_metric_the_yml_defines(catalog):
    from contextlib import nullcontext

    from agent.tools import warehouse_tools

    con = warehouse(LINES)
    tools = {tool.name: tool for tool in warehouse_tools(lambda: nullcontext(con), catalog)}
    assert list(tools) == ["explain_change", "describe_model", "query_metric", "run_scenario"]
    schema = tools["query_metric"].schema["function"]["parameters"]["properties"]
    assert schema["metrics"]["items"]["enum"] == sorted(catalog.layer.metrics)
    # Names bare rather than in a list, as small models send them.
    result = tools["query_metric"].run({"metrics": "orders", "group_by": "year", "years": 2022})
    assert result.note is None
    assert result.text.splitlines()[:3] == ["orders by year, 2022", "year  orders", "2022       2"]
    # A name given twice is answered once, and an empty list of years is no years.
    twice = tools["query_metric"].run({"metrics": ["orders", "orders"], "years": []})
    assert twice.text.splitlines()[1:] == ["orders", "     4"]


def test_a_column_is_described_by_its_whole_first_sentence(catalog):
    from agent.catalog import _first, describe_model

    # The full stop that closes "i.e." or "e.g." is not the end of a sentence.
    assert _first("X, i.e. Y. Z.") == "X, i.e. Y."
    assert _first("A key, e.g. `a.b`. B.") == "A key, e.g. `a.b`."
    assert _first("Made in Chile. More.") == "Made in Chile."
    lines = describe_model(catalog, "fct_cbam_exposure").splitlines()
    good_key = next(line for line in lines if line.startswith("- good_key "))
    assert good_key.endswith("e.g. `28041000-hydrogen`.")
    certificates = next(line for line in lines if line.startswith("- certificates_2026_"))
    assert "before any deduction" in certificates
