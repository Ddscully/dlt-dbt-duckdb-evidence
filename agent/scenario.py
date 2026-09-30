"""A CBAM scenario: what a tonne of an imported good costs at a carbon price you choose.

`marts.fct_cbam_exposure` holds, for each (country the annex lists, good), the
certificates the regulation's default values imply per tonne of good in 2026,
2027 and 2028, and prices them at one assumed EU ETS price, the dbt var
`eu_ets_price_eur_per_t`. The tonnage is fixed by law and the price is not, so
this module re-prices the certificate columns at any price without a dbt
rebuild, for one good at a time.

A result shows the good's cheapest, median and dearest *listed* source, the
annex's "other countries and territories" table (the fallback), and any
countries asked for. Each named country is marked as carrying its own value
or the fallback's, since the annex prints "-" for about one row in fifteen and
the mart copies the fallback onto it. A country the annex does not list for the
good is not priced at all: the fallback applies to an unlisted country only if
CBAM covers it, and which countries it exempts is set by the regulation's
articles, which this tool does not know.

**The figures are gross.** An importer surrenders fewer certificates than the
defaults imply, after two deductions. One is in step with the EU ETS allowances
EU producers still receive free: a benchmark per good, times a factor that
phases out from 2026 to 2034, and those benchmarks are provisional until
1 January 2027. The other is for any carbon price effectively paid where the
good was made (Article 9 of Regulation (EU) 2023/956), whose rules were not yet
adopted when this was written. Nothing in the warehouse models either, so every
result carries a note saying the figure is before both.
`docs/decisions/0017-the-cbam-scenario.md` has the reasons.

**The arithmetic is `Decimal`.** The mart's doubles carry noise (China's 2027
aluminium certificates read `3.5999999999999996`); the annex prints three
decimals and the mark-up adds at most two more, so each value is rounded to
five places, and euros to the penny, half up, as `scripts/build_cbam_seeds.py`
rounds the annex.

A good is named in words, because a model cannot choose from 260 keys in a
schema: an exact `good_key`, a CN code or its prefix, or words from the
description and product group. Several matches come back as a list to choose
from, not as an error.

Run:  uv run python -m agent.scenario 7601 2027 100 --country China --tonnes 500
"""

from __future__ import annotations

import argparse
import difflib
import statistics
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import duckdb

from modern_data_stack.paths import warehouse_path

# The tables a scenario reads, which `describe_model` also offers.
MODELS = ("fct_cbam_exposure",)

# The mart's certificate columns, one per year of the phase-in schedule.
# Interpolated into the SQL, so this mapping is the allowlist.
CERTIFICATE_COLUMNS = {
    2026: "certificates_2026_t_co2e_per_t",
    2027: "certificates_2027_t_co2e_per_t",
    2028: "certificates_2028_t_co2e_per_t",
}
YEARS = tuple(CERTIFICATE_COLUMNS)
MAX_CANDIDATES = 25

_PLACES = Decimal("0.00001")
_PENNY = Decimal("0.01")

_GOODS_SQL = """
select distinct good_key, cn_code, product_group, goods_description
from marts.fct_cbam_exposure
"""


@dataclass(frozen=True)
class Good:
    good_key: str
    cn_code: str
    product_group: str
    description: str


@dataclass(frozen=True)
class Row:
    name: str  # cheapest, median, dearest, fallback, or the country asked for
    label: str  # what the table prints
    certificates: Decimal  # tCO2e per tonne of good


@dataclass(frozen=True)
class Scenario:
    good: Good
    year: int
    price: Decimal  # EUR per tonne of CO2e, the scenario's
    build_price: Decimal  # the one the warehouse was built with
    tonnes: Decimal | None
    listed: int  # sources the annex lists for this good
    rows: tuple[Row, ...]
    unlisted: tuple[str, ...]  # countries asked for that the annex does not list for it


@dataclass(frozen=True)
class Candidates:
    term: str
    goods: tuple[Good, ...]  # the first MAX_CANDIDATES, by CN code
    matched: int


def euros(certificates: Decimal, price: Decimal, tonnes: Decimal = Decimal(1)) -> Decimal:
    """The cost, rounded to the penny once, after every multiplication.

    `Decimal` keeps 28 digits, and refuses a cost with more before the pennies
    than that leaves room for; the refusal is passed on as one a model can act on.
    """
    try:
        return (certificates * price * tonnes).quantize(_PENNY, ROUND_HALF_UP)
    except InvalidOperation:
        raise ValueError(
            "too large to price to the penny; lower ets_price_eur_per_t or tonnes"
        ) from None


def _certificates(value: float) -> Decimal:
    return Decimal(repr(value)).quantize(_PLACES, ROUND_HALF_UP)


def _positive(value, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise TypeError(f"{name} is a number, not {value!r}")
    try:
        number = Decimal(str(value))
    except ArithmeticError:
        raise ValueError(f"{name} is a number, not {value!r}") from None
    if not number.is_finite() or number <= 0:
        raise ValueError(f"{name} must be above zero, not {value!r}")
    return number


def _year(value) -> int:
    if isinstance(value, bool):
        raise TypeError(f"year is one of {', '.join(map(str, YEARS))}, not {value!r}")
    try:
        year = int(value)
    except (TypeError, ValueError):
        year = None
    if year not in CERTIFICATE_COLUMNS:
        raise ValueError(
            f"year is one of {', '.join(map(str, YEARS))}, the years the annex's "
            f"mark-up schedule covers, not {value!r}"
        )
    return year


def find_goods(con: duckdb.DuckDBPyConnection, term: str) -> list[Good]:
    """The goods `term` names: an exact key, else a CN prefix, else every word."""
    term = term.strip()
    if not term:
        raise ValueError("good is a CN code, a good_key, or words from its description")
    goods = [Good(*row) for row in con.execute(_GOODS_SQL).fetchall()]
    exact = [g for g in goods if g.good_key == term]
    if exact:
        return exact
    digits = term.replace(" ", "")
    if digits.isdigit():
        found = [g for g in goods if g.cn_code.replace(" ", "").startswith(digits)]
    else:
        words = term.casefold().split()
        found = [
            g
            for g in goods
            if all(w in f"{g.description} {g.product_group}".casefold() for w in words)
        ]
    return sorted(found, key=lambda g: (g.cn_code.replace(" ", ""), g.good_key))


def _countries(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    """Every spelling of a country the warehouse knows, casefolded, to its display name.

    The World Bank's names, the annex's labels and ISO3 codes, whether or not the
    annex lists the country for a given good.
    """
    rows = con.execute(
        """
        select country_name, country_name, country_iso3 from marts.dim_country
        union all
        select country_display_name, country_or_territory, country_iso3
        from marts.fct_cbam_exposure
        where not is_fallback_table
        """
    ).fetchall()
    return {
        spelling.casefold(): name
        for name, label, iso3 in rows
        for spelling in (name, label, iso3)
        if spelling
    }


def _unknown_country(asked: str, known: dict[str, str]) -> ValueError:
    """The refusal, with the spellings a model most likely meant ("Turkey" is Turkiye)."""
    key = asked.strip().casefold()
    near = {known[s] for s in difflib.get_close_matches(key, known, n=3, cutoff=0.75)}
    near |= {name for spelling, name in known.items() if len(key) > 3 and key in spelling}
    hint = f" Did you mean {' or '.join(sorted(near)[:5])}?" if near else ""
    return ValueError(
        f"no country {asked!r}; name it as the World Bank does, or by ISO3 code.{hint}"
    )


def run_scenario(
    con: duckdb.DuckDBPyConnection,
    good: str,
    year,
    ets_price_eur_per_t,
    countries: list[str] | tuple[str, ...] = (),
    tonnes=None,
) -> Scenario | Candidates:
    """One good's CBAM cost per tonne at `ets_price_eur_per_t`, or the goods to choose from."""
    year = _year(year)
    price = _positive(ets_price_eur_per_t, "ets_price_eur_per_t")
    tonnes = None if tonnes is None else _positive(tonnes, "tonnes")
    goods = find_goods(con, good)
    if not goods:
        raise ValueError(
            f"no CBAM good matches {good!r}; name it by CN code (e.g. 7601), "
            "good_key, or words from its description"
        )
    if len(goods) > 1:
        return Candidates(good, tuple(goods[:MAX_CANDIDATES]), len(goods))
    (chosen,) = goods

    column = CERTIFICATE_COLUMNS[year]
    rows = con.execute(
        f"""
        select country_display_name, country_or_territory, country_iso3,
               is_fallback_table, is_country_specific, {column}, ets_price_eur_per_t
        from marts.fct_cbam_exposure
        where good_key = $good
        order by country_display_name
        """,
        {"good": chosen.good_key},
    ).fetchall()
    build_price = Decimal(str(rows[0][6]))
    listed = [(r[0], _certificates(r[5])) for r in rows if not r[3]]
    fallback = [_certificates(r[5]) for r in rows if r[3]]

    out: list[Row] = []
    if listed:
        values = [value for _, value in listed]
        for name, value in (("cheapest", min(values)), ("dearest", max(values))):
            tied = [country for country, v in listed if v == value]
            others = f" and {len(tied) - 1} others" if len(tied) > 1 else ""
            out.append(Row(name, f"{name} listed source: {tied[0]}{others}", value))
        median = Decimal(statistics.median(values)).quantize(_PLACES, ROUND_HALF_UP)
        out.insert(1, Row("median", f"median of {len(listed)} listed sources", median))
    if fallback:
        out.append(Row("fallback", "other countries and territories (fallback)", fallback[0]))

    by_name = {r[0]: r for r in rows if not r[3]}
    known = _countries(con) if countries else {}
    unlisted = []
    for asked in countries:
        name = known.get(asked.strip().casefold())
        if name is None:
            raise _unknown_country(asked, known)
        if name in by_name:
            _, _, _, _, own, value, _ = by_name[name]
            basis = "own value" if own else "fallback's value"
            out.append(Row(name, f"{name} ({basis})", _certificates(value)))
        else:
            unlisted.append(name)
    return Scenario(
        chosen, year, price, build_price, tonnes, len(listed), tuple(out), tuple(unlisted)
    )


def _money(value: Decimal, signed: bool = False) -> str:
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{'-' if value < 0 else ''}€{abs(value):,.2f}"


def _number(value: Decimal) -> str:
    text = f"{value.normalize():f}"
    return f"{int(value):,}" if value == value.to_integral_value() else text


def scenario_note(result: Scenario | Candidates) -> str | None:
    """What every answer carries verbatim: the figures are gross and the price assumed."""
    if not isinstance(result, Scenario):
        return None
    return (
        f"These are gross figures, before the deductions for EU ETS free allocation "
        f"and for any carbon price paid where the good was made, which this tool "
        f"does not model, so they are not what an importer will owe. "
        f"They use the regulation's default values, not any supplier's verified "
        f"emissions, and €{_number(result.price)} per tonne of CO2e is an assumed price."
    )


def render(result: Scenario | Candidates) -> str:
    if isinstance(result, Candidates):
        more = (
            f"{result.matched} goods match {result.term!r}; the first "
            f"{len(result.goods)} by CN code:"
            if result.matched > len(result.goods)
            else f"{result.matched} goods match {result.term!r}:"
        )
        lines = [more]
        for g in result.goods:
            description = g.description if len(g.description) <= 80 else g.description[:79] + "…"
            lines.append(f"  {g.good_key}  CN {g.cn_code}  {g.product_group}: {description}")
        lines.append("Call again with one good_key, or a narrower term.")
        return "\n".join(lines)

    s = result
    repriced = s.price != s.build_price
    header = [
        "",
        "tCO2e per t",
        f"€ per t at €{_number(s.price)}",
    ]
    if repriced:
        header += [f"€ per t at €{_number(s.build_price)}", "change per t"]
    if s.tonnes is not None:
        header.append(f"€ for {_number(s.tonnes)} t at €{_number(s.price)}")
    table = [header]
    for row in s.rows:
        cells = [row.label, _number(row.certificates), _money(euros(row.certificates, s.price))]
        if repriced:
            at_build = euros(row.certificates, s.build_price)
            cells += [
                _money(at_build),
                _money(euros(row.certificates, s.price) - at_build, signed=True),
            ]
        if s.tonnes is not None:
            cells.append(_money(euros(row.certificates, s.price, s.tonnes)))
        table.append(cells)
    widths = [max(len(r[i]) for r in table) for i in range(len(header))]
    lines = [
        f"{s.good.description} (CN {s.good.cn_code}, {s.good.product_group}): CBAM "
        f"certificates for {s.year}, priced at €{_number(s.price)} per tonne of CO2e"
        + (f" (the warehouse assumes €{_number(s.build_price)})." if repriced else "."),
        "",
    ]
    for cells in table:
        first = cells[0].ljust(widths[0])
        rest = "  ".join(c.rjust(w) for c, w in zip(cells[1:], widths[1:], strict=True))
        lines.append(f"{first}  {rest}".rstrip())
    by_name = {row.name: row.certificates for row in s.rows}
    if "cheapest" in by_name and "dearest" in by_name:
        # The printed figures' difference, so it agrees with the table above.
        def gap(price: Decimal, tonnes: Decimal = Decimal(1)) -> str:
            dearest = euros(by_name["dearest"], price, tonnes)
            return _money(dearest - euros(by_name["cheapest"], price, tonnes))

        lines += [
            "",
            f"The dearest listed source costs {gap(s.price)} more per tonne than the "
            f"cheapest at €{_number(s.price)}"
            + (f" ({gap(s.build_price)} at €{_number(s.build_price)})" if repriced else "")
            + (f", and {gap(s.price, s.tonnes)} more for {_number(s.tonnes)} t" if s.tonnes else "")
            + ".",
        ]
    for name in s.unlisted:
        lines.append(
            f"{name}: not listed in the annex for this good. An unlisted country outside "
            "the EU uses the other-countries values above, unless CBAM exempts it; this "
            "tool does not know the exemptions."
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("good", help="a CN code, a good_key, or words from the description")
    parser.add_argument("year", type=int, choices=YEARS)
    parser.add_argument("price", type=float, help="EUR per tonne of CO2e")
    parser.add_argument("--country", action="append", default=[], dest="countries")
    parser.add_argument("--tonnes", type=float)
    args = parser.parse_args()
    # Read-only: fails while a build holds the file, by DuckDB's design.
    with duckdb.connect(warehouse_path(), read_only=True) as con:
        result = run_scenario(con, args.good, args.year, args.price, args.countries, args.tonnes)
        print(render(result))
        note = scenario_note(result)
        if note:
            print(f"\n{note}")


if __name__ == "__main__":
    main()
