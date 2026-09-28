"""The CBAM scenario (`agent/scenario.py`) against a table small enough to price by hand.

What they hold:

* each row's certificates and euros equal a value worked out on paper, with the
  fallback kept out of the listed sources, a fallen-back country marked as one,
  and half a penny rounded up;
* a good named by key, CN prefix or words resolves to one good, or to a list to
  choose from;
* a country the annex does not list for the good is named and never priced,
  and a year, price or country that cannot be answered is refused.
"""

from __future__ import annotations

from decimal import Decimal

import duckdb
import pytest

from agent.scenario import Candidates, Scenario, euros, render, run_scenario, scenario_note
from agent.tools import call_tool, warehouse_tools

ALU = ("7601-unwrought-aluminium", "7601", "Aluminium", "Unwrought aluminium")
BARS = ("76041010-bars-and-rods", "7604 10 10", "Aluminium", "Bars and rods")
BARS_ALLOY = ("76042910-bars-and-rods", "7604 29 10", "Aluminium", "Bars and rods")
STEEL = (
    "72071111-semi-finished",
    "7207 11 11",
    "Iron and steel",
    "Semi-finished products, of non-alloy free-cutting steel",
)
FALLBACK = ("Other countries and territories", "Other countries and territories", None)

# (good, (display name, annex label, iso3), fallback table?, own value?, 2027 certificates)
ROWS = [
    (ALU, ("Aland", "Aland", "ALA"), False, True, 0.5),
    (ALU, ("Bland", "Bland", "BLA"), False, True, 0.5),
    (ALU, ("Cland", "Cland", "CLA"), False, True, 3.5999999999999996),
    (ALU, ("Dland", "Dland", "DLA"), False, False, 2.6435999999999997),
    (ALU, ("Eland", "Eland", "ELA"), False, True, 0.08625),
    (ALU, ("Fland", "Fland", "FLA"), False, True, 3.6),
    (ALU, FALLBACK, True, True, 2.6435999999999997),
    (BARS, ("Aland", "Aland", "ALA"), False, True, 1.0),
    (BARS, FALLBACK, True, True, 2.0),
    (BARS_ALLOY, ("Aland", "Aland", "ALA"), False, True, 1.0),
    (BARS_ALLOY, FALLBACK, True, True, 2.0),
    (STEEL, ("Aland", "Aland", "ALA"), False, True, 1.0),
    (STEEL, FALLBACK, True, True, 2.0),
]


@pytest.fixture
def con():
    con = duckdb.connect()
    con.execute("create schema marts")
    con.execute(
        """
        create table marts.fct_cbam_exposure (
            good_key varchar, cn_code varchar, product_group varchar,
            goods_description varchar, country_display_name varchar,
            country_or_territory varchar, country_iso3 varchar,
            is_fallback_table boolean, is_country_specific boolean,
            certificates_2026_t_co2e_per_t double, certificates_2027_t_co2e_per_t double,
            certificates_2028_t_co2e_per_t double, ets_price_eur_per_t integer
        )
        """
    )
    con.executemany(
        "insert into marts.fct_cbam_exposure values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 75)",
        [
            (*good, *place, fallback, own, value / 2, value, value * 2)
            for good, place, fallback, own, value in ROWS
        ],
    )
    con.execute("create table marts.dim_country (country_iso3 varchar, country_name varchar)")
    con.execute("insert into marts.dim_country values ('DEU', 'Germany'), ('ALA', 'Aland')")
    return con


def _rows(result: Scenario) -> dict[str, tuple[str, Decimal]]:
    return {row.name: (row.label, row.certificates) for row in result.rows}


def test_each_row_matches_its_hand_value(con):
    result = run_scenario(con, "7601", 2027, 100, countries=["Dland", "cla"], tonnes=3)
    assert isinstance(result, Scenario)
    assert result.build_price == 75 and result.listed == 6
    assert _rows(result) == {
        "cheapest": ("cheapest listed source: Eland", Decimal("0.08625")),
        # Six listed values; the fallback's copy on Dland counts, the fallback does not.
        "median": ("median of 6 listed sources", Decimal("1.5718")),  # (0.5 + 2.6436) / 2
        "dearest": ("dearest listed source: Cland and 1 others", Decimal("3.6")),
        "fallback": ("other countries and territories (fallback)", Decimal("2.6436")),
        "Dland": ("Dland (fallback's value)", Decimal("2.6436")),
        "Cland": ("Cland (own value)", Decimal("3.6")),
    }
    # 0.08625 × 100 is 8.625: half up gives 8.63 where round() would give 8.62.
    assert euros(Decimal("0.08625"), result.price) == Decimal("8.63")
    assert euros(Decimal("0.08625"), result.build_price) == Decimal("6.47")  # 6.46875
    # Rounded once, after the tonnes: 25.875, not 3 × 8.63.
    assert result.tonnes == 3
    assert euros(Decimal("0.08625"), result.price, Decimal(3)) == Decimal("25.88")
    text = render(result)
    assert "€8.63" in text and "€6.47" in text and "+€2.16" in text and "€25.88" in text
    assert "(the warehouse assumes €75)" in text
    # The gap is the printed figures' difference: €360.00 − €8.63, not 351.375 rounded.
    assert (
        "The dearest listed source costs €351.37 more per tonne than the cheapest at €100 "
        "(€263.53 at €75), and €1,054.12 more for 3 t." in text
    )


def test_the_note_says_the_figures_are_gross(con):
    result = run_scenario(con, "7601", 2027, 100)
    note = scenario_note(result)
    assert note is not None
    assert note.startswith("These are gross figures, before the deductions for EU ETS free")
    assert "carbon price paid where the good was made" in note
    assert "€100 per tonne of CO2e is an assumed price" in note


@pytest.mark.parametrize(
    ("term", "key"),
    [
        ("76041010-bars-and-rods", "76041010-bars-and-rods"),  # an exact key
        ("7604 29", "76042910-bars-and-rods"),  # a CN prefix, spaced as typed
        ("semi-finished NON-ALLOY", "72071111-semi-finished"),  # words, any case
    ],
)
def test_a_good_resolves_from_key_code_or_words(con, term, key):
    result = run_scenario(con, term, 2026, 75)
    assert isinstance(result, Scenario) and result.good.good_key == key


def test_several_matches_are_a_list_not_an_error(con):
    # "Bars and rods" twice: only the CN code tells them apart, and the product
    # group counts as words, since the description never says aluminium.
    for term in ("7604", "aluminium bars"):
        result = run_scenario(con, term, 2026, 75)
        assert isinstance(result, Candidates) and result.matched == 2
        assert [g.cn_code for g in result.goods] == ["7604 10 10", "7604 29 10"]
        assert scenario_note(result) is None
    with pytest.raises(ValueError, match="no CBAM good matches 'titanium'"):
        run_scenario(con, "titanium", 2026, 75)


def test_an_unlisted_country_is_named_and_never_priced(con):
    result = run_scenario(con, "7601", 2027, 100, countries=["Germany"])
    assert isinstance(result, Scenario)
    assert result.unlisted == ("Germany",)
    assert "Germany" not in _rows(result)
    assert "Germany: not listed in the annex for this good" in render(result)
    # Listed for another good is still unlisted for this one.
    listed = run_scenario(con, "7601", 2027, 100, countries=["Aland"])
    assert isinstance(listed, Scenario) and listed.unlisted == ()
    with pytest.raises(ValueError, match="no country 'Narnia'"):
        run_scenario(con, "7601", 2027, 100, countries=["Narnia"])
    # A near miss names the spelling the warehouse uses.
    with pytest.raises(ValueError, match="Did you mean Germany"):
        run_scenario(con, "7601", 2027, 100, countries=["Germeny"])


@pytest.mark.parametrize(
    ("year", "price", "tonnes", "error"),
    [
        (2029, 75, None, "year is one of 2026, 2027, 2028"),
        (2026, 0, None, "ets_price_eur_per_t must be above zero"),
        (2026, -5, None, "ets_price_eur_per_t must be above zero"),
        (2026, "a lot", None, "ets_price_eur_per_t is a number"),
        (2026, 75, 0, "tonnes must be above zero"),
    ],
)
def test_what_cannot_be_answered_is_refused(con, year, price, tonnes, error):
    with pytest.raises(ValueError, match=error):
        run_scenario(con, "7601", year, price, tonnes=tonnes)


def test_through_the_tool_list(con):
    tools = {tool.name: tool for tool in warehouse_tools(con)}
    priced = call_tool(
        tools, "run_scenario", {"good": "7601", "year": 2027, "ets_price_eur_per_t": 90}
    )
    assert priced.text.startswith("Unwrought aluminium (CN 7601, Aluminium)")
    assert priced.note is not None and "gross" in priced.note
    listed = call_tool(
        tools, "run_scenario", {"good": "7604", "year": 2027, "ets_price_eur_per_t": 90}
    )
    assert listed.text.startswith("2 goods match '7604'") and listed.note is None
    # A lone country arrives bare from a small model.
    bare = call_tool(
        tools,
        "run_scenario",
        {"good": "7601", "year": 2027, "ets_price_eur_per_t": 90, "countries": "Cland"},
    )
    assert "Cland (own value)" in bare.text
    missing = call_tool(tools, "run_scenario", {"good": "7601"})
    assert missing.text == "error: run_scenario needs ets_price_eur_per_t and year"
