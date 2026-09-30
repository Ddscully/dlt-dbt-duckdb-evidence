"""The revenue bridge: why net revenue moved between two years, in bars that add up.

A finance reader asks "revenue fell 7% — why?", and the answer is a waterfall:
how much came from selling fewer units, from selling a different mix of
products, from charging different prices, from products that came and went,
from returns, and from the exchange rate. This module computes that waterfall
over `marts.fct_retail_order_line` in deterministic Python. A model may *call*
it; it never does the arithmetic.

**Net revenue is the warehouse's own definition**, `sum(line_amount_gbp)
filter (where is_revenue_line)` as `_retail.yml` states it — product sales net
of product cancellations. Everything else invoiced (postage, fees, bad-debt and
manual adjustments, discounts, samples, stock write-offs) is set aside as one
final step, so the bridge still reconciles to everything invoiced and the
answer names what it left out. `docs/decisions/0014-the-revenue-bridge-bars.md`
has the bar set and what it was chosen over.

**Periods are aligned, and the answer says so.** The extract runs from
2009-12-01 to 2011-12-09, so 2010 against 2011 naively compares a full year
with one missing 22 days, and reports a fall that is partly missing days. Both
years are cut to the common month-day window — 1 Jan–9 Dec there — taken from
the *fact's* first and last dates. Never from each year's own last sale: 2010's
is 23 Dec, because the business closes over New Year, and reading that as the
end of coverage would cut real trading days out of every other year.
`docs/decisions/0013-compare-aligned-periods.md` has the measurement.

The bars, on sale lines of SKUs sold in both years (the *continuing* SKUs),
with `q` units and `r` GBP per SKU and `p = r / q`:

* **volume** — `(Q_b − Q_a) · P̄_a`, the change in units at year a's average price
* **mix** — `Σ q_b·p_a − Q_b · P̄_a`, year b's units re-weighted across SKUs at
  year a's prices
* **price** — `Σ q_b·(p_b − p_a)`, year b's units at the change in each SKU's price

then **new SKUs** (sold only in b, their whole revenue) and **discontinued
SKUs** (sold only in a, minus theirs), because a SKU sold in one year only has
no price change to measure; **returns**, the change in product cancellations,
kept out of the price and volume effects because a SKU's net units can be zero
or negative; and, outside GBP, **FX**. The GBP effects are converted at year
a's effective rate (`x_a`, net revenue in the currency over net revenue in
GBP), and FX is what is left: `NR_b,cur − x_a · NR_b,gbp`.

**Every sum is a `Fraction`.** Each aggregate the query returns is a double,
and `Fraction(double)` is exact, so the identity "the bars sum to the change"
holds with `==` rather than a tolerance. Summed as floats, the seven GBP bars
for 2010→2011 miss by about 1e-9 — small, and enough to make the check
meaningless as a test.

Run:  uv run python -m agent.bridge 2010 2011 --currency EUR
"""

from __future__ import annotations

import argparse
import calendar
import datetime as dt
from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction

import duckdb

from modern_data_stack.paths import warehouse_path

# The fact's money columns, one per reporting currency. Interpolated into the
# SQL, so this mapping is the allowlist.
AMOUNT_COLUMNS = {
    "GBP": "line_amount_gbp",
    "EUR": "line_amount_eur",
    "USD": "line_amount_usd",
}
SYMBOLS = {"GBP": "£", "EUR": "€", "USD": "$"}

MonthDay = tuple[int, int]
FULL_YEAR: tuple[MonthDay, MonthDay] = ((1, 1), (12, 31))

# Grouped to one row per (year, bucket, key): SKUs for sale lines, the item
# type for everything net revenue leaves out. `key` is a reserved word.
_BRIDGE_SQL = """
select
    year,
    case
        when not is_revenue_line then 'non_product'
        when invoice_type = 'sale' then 'sale'
        else 'return'
    end as bucket,
    case
        when is_revenue_line and invoice_type = 'sale' then stock_code
        when is_stock_write_off then 'stock_write_off'
        else item_type
    end as line_key,
    sum(quantity) as units,
    sum(line_amount_gbp) as amount_gbp,
    sum({amount}) as amount_cur,
    count(*) - count({amount}) as missing_cur
from marts.fct_retail_order_line
where (year = $a and invoice_date between $a_start and $a_end)
   or (year = $b and invoice_date between $b_start and $b_end)
group by all
"""


@dataclass(frozen=True)
class Bar:
    name: str
    label: str
    value: Fraction


@dataclass(frozen=True)
class Bridge:
    year_a: int
    year_b: int
    currency: str
    coverage: tuple[dt.date, dt.date]
    window: tuple[MonthDay, MonthDay]
    days_a: int
    days_b: int
    net_revenue_a: Fraction
    net_revenue_b: Fraction
    # Sum exactly to net_revenue_b − net_revenue_a.
    bars: tuple[Bar, ...]
    # Takes net revenue to everything invoiced; `set_aside` is its detail.
    non_product: Bar
    set_aside: dict[str, Fraction]
    invoiced_a: Fraction
    invoiced_b: Fraction
    # The relative change a full-year comparison would have reported, when
    # the window is not the full year.
    unaligned_change: Fraction | None
    continuing_skus: int
    new_skus: int
    discontinued_skus: int

    @property
    def aligned(self) -> bool:
        return self.window != FULL_YEAR

    @property
    def change(self) -> Fraction:
        return self.net_revenue_b - self.net_revenue_a

    @property
    def change_pct(self) -> Fraction:
        return self.change / self.net_revenue_a


def _date(year: int, month_day: MonthDay) -> dt.date:
    # A window ending 29 Feb, applied to a year without one.
    month, day = month_day
    return dt.date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _covered(year: int, coverage: tuple[dt.date, dt.date]) -> tuple[MonthDay, MonthDay]:
    first, last = coverage
    if not first.year <= year <= last.year:
        raise ValueError(f"{year} is outside the data, which covers {first} to {last}")
    start = (first.month, first.day) if first.year == year else (1, 1)
    end = (last.month, last.day) if last.year == year else (12, 31)
    return start, end


def common_window(
    year_a: int, year_b: int, coverage: tuple[dt.date, dt.date]
) -> tuple[MonthDay, MonthDay]:
    """The month-day window both years are covered for, from the fact's bounds."""
    (start_a, end_a), (start_b, end_b) = _covered(year_a, coverage), _covered(year_b, coverage)
    window = (max(start_a, start_b), min(end_a, end_b))
    if window[0] > window[1]:
        raise ValueError(f"{year_a} and {year_b} share no covered days")
    return window


def _load(
    con: duckdb.DuckDBPyConnection,
    year_a: int,
    year_b: int,
    window: tuple[MonthDay, MonthDay],
    currency: str,
) -> list[tuple]:
    sql = _BRIDGE_SQL.format(amount=AMOUNT_COLUMNS[currency])
    params = {
        "a": year_a,
        "b": year_b,
        "a_start": _date(year_a, window[0]),
        "a_end": _date(year_a, window[1]),
        "b_start": _date(year_b, window[0]),
        "b_end": _date(year_b, window[1]),
    }
    rows = con.execute(sql, params).fetchall()
    # A null amount drops out of a sum without a word, and the bars would
    # no longer describe the lines they claim to.
    if any(row[6] for row in rows):
        raise ValueError(f"lines in the window have no {currency} amount")
    return rows


Totals = dict[tuple[int, str], list[Fraction]]


def _totals(rows: list[tuple]) -> Totals:
    """(year, bucket) → [GBP, reporting currency], exact; a missing pair is zero."""
    totals: Totals = defaultdict(lambda: [Fraction(0), Fraction(0)])
    for year, bucket, _key, _units, gbp, cur, _missing in rows:
        totals[(year, bucket)][0] += Fraction(gbp)
        totals[(year, bucket)][1] += Fraction(cur)
    return totals


def _net_revenue(totals: Totals, year: int) -> tuple[Fraction, Fraction]:
    """(GBP, reporting currency): product sales net of product cancellations."""
    sale, cancelled = totals[(year, "sale")], totals[(year, "return")]
    return sale[0] + cancelled[0], sale[1] + cancelled[1]


def _sum(values) -> Fraction:
    return sum(values, Fraction(0))


def explain_change(
    con: duckdb.DuckDBPyConnection, year_a: int, year_b: int, currency: str = "EUR"
) -> Bridge:
    """Bridge net revenue from `year_a` to `year_b`, in `currency`."""
    currency = currency.upper()
    if currency not in AMOUNT_COLUMNS:
        raise ValueError(f"currency must be one of {', '.join(AMOUNT_COLUMNS)}")
    if year_a == year_b:
        # Not an empty bridge: every bar would read zero, under a note about alignment.
        raise ValueError(f"year_a and year_b are both {year_a}; name two different years")
    bounds = con.execute(
        "select min(invoice_date), max(invoice_date) from marts.fct_retail_order_line"
    ).fetchone()
    if bounds is None or bounds[0] is None:
        raise ValueError("marts.fct_retail_order_line has no lines")
    coverage = (bounds[0], bounds[1])
    window = common_window(year_a, year_b, coverage)
    rows = _load(con, year_a, year_b, window, currency)
    totals = _totals(rows)

    # Per-SKU (units, GBP) on sale lines; the non-product change per item type.
    skus: dict[int, dict[str, tuple[Fraction, Fraction]]] = {year_a: {}, year_b: {}}
    set_aside: dict[str, Fraction] = defaultdict(Fraction)
    for year, bucket, key, units, gbp, cur, _missing in rows:
        if bucket == "sale":
            skus[year][key] = (Fraction(units), Fraction(gbp))
        elif bucket == "non_product":
            set_aside[key] += Fraction(cur) if year == year_b else -Fraction(cur)

    net_gbp_a, net_a = _net_revenue(totals, year_a)
    net_gbp_b, net_b = _net_revenue(totals, year_b)
    if not net_gbp_a:
        raise ValueError(f"{year_a} has no net revenue in the window to bridge from")

    sold_a, sold_b = skus[year_a], skus[year_b]
    continuing = sold_a.keys() & sold_b.keys()
    new, discontinued = sold_b.keys() - sold_a.keys(), sold_a.keys() - sold_b.keys()
    units_a = _sum(sold_a[s][0] for s in continuing)
    units_b = _sum(sold_b[s][0] for s in continuing)
    revenue_a = _sum(sold_a[s][1] for s in continuing)
    revenue_b = _sum(sold_b[s][1] for s in continuing)
    # Year b's units at year a's prices.
    b_at_a_prices = _sum(sold_b[s][0] * sold_a[s][1] / sold_a[s][0] for s in continuing)
    average_price_a = revenue_a / units_a if units_a else Fraction(0)

    gbp_effects = [
        (
            "volume",
            f"Volume: units at {year_a}'s average price",
            (units_b - units_a) * average_price_a,
        ),
        ("mix", "Mix: which SKUs the units went to", b_at_a_prices - units_b * average_price_a),
        ("price", "Price: each SKU's own price change", revenue_b - b_at_a_prices),
        ("new_skus", f"New SKUs (sold in {year_b}, not {year_a})", _sum(sold_b[s][1] for s in new)),
        (
            "discontinued_skus",
            f"Discontinued SKUs (sold in {year_a}, not {year_b})",
            -_sum(sold_a[s][1] for s in discontinued),
        ),
        (
            "returns",
            "Returns (product cancellations)",
            totals[(year_b, "return")][0] - totals[(year_a, "return")][0],
        ),
    ]
    rate_a = net_a / net_gbp_a
    bars = [Bar(name, label, value * rate_a) for name, label, value in gbp_effects]
    if currency != "GBP":
        bars.append(Bar("fx", f"FX: the GBP to {currency} rate", net_b - rate_a * net_gbp_b))

    non_product_a = totals[(year_a, "non_product")][1]
    non_product_b = totals[(year_b, "non_product")][1]
    non_product = Bar("non_product", "Non-product lines", non_product_b - non_product_a)

    unaligned_change = None
    if window != FULL_YEAR:
        full = _totals(_load(con, year_a, year_b, FULL_YEAR, currency))
        full_a, full_b = _net_revenue(full, year_a)[1], _net_revenue(full, year_b)[1]
        unaligned_change = full_b / full_a - 1 if full_a else None

    def days(year: int) -> int:
        return (_date(year, window[1]) - _date(year, window[0])).days + 1

    return Bridge(
        year_a=year_a,
        year_b=year_b,
        currency=currency,
        coverage=coverage,
        window=window,
        days_a=days(year_a),
        days_b=days(year_b),
        net_revenue_a=net_a,
        net_revenue_b=net_b,
        bars=tuple(bars),
        non_product=non_product,
        set_aside=dict(set_aside),
        invoiced_a=net_a + non_product_a,
        invoiced_b=net_b + non_product_b,
        unaligned_change=unaligned_change,
        continuing_skus=len(continuing),
        new_skus=len(new),
        discontinued_skus=len(discontinued),
    )


def _money(value: Fraction, symbol: str, signed: bool = False) -> str:
    sign = "-" if value < 0 else "+" if signed else ""
    return f"{sign}{symbol}{abs(float(value)) / 1000:,.1f}k"


def _month_day(month_day: MonthDay) -> str:
    return f"{month_day[1]} {calendar.month_abbr[month_day[0]]}"


# Subtotals printed above their bars. The smoke test against a local model
# found it adding bars up itself, and getting the sums wrong.
GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Continuing SKUs (sold in both years)", ("volume", "mix", "price")),
    ("Catalogue churn", ("new_skus", "discontinued_skus")),
)


def alignment_note(bridge: Bridge) -> str | None:
    """What an answer must say when the years were cut to a common window.

    A function rather than a line of `render` so a caller that paraphrases the
    bridge can still append it verbatim.
    """
    if not bridge.aligned:
        return None
    first, last = bridge.coverage
    start, end = bridge.window
    spans = " and ".join(
        f"{_date(year, start)} to {_date(year, end)}" for year in (bridge.year_a, bridge.year_b)
    )
    note = (
        f"Periods aligned: the data covers {first} to {last}, so each year is compared over "
        f"{_month_day(start)} to {_month_day(end)} only ({spans}; {bridge.days_a} and "
        f"{bridge.days_b} days)."
    )
    if bridge.unaligned_change is not None:
        note += (
            f" Over the full calendar years the change would read "
            f"{float(bridge.unaligned_change):+.2%}, against {float(bridge.change_pct):+.2%} aligned."
        )
    return note


def render(bridge: Bridge) -> str:
    """The bridge as plain text: headline, alignment note, grouped bars, what was set aside.

    Written for a reader who will quote it, human or model: every subtotal a
    reader would want is printed, so nothing needs adding up.
    """
    symbol = SYMBOLS[bridge.currency]
    base = bridge.net_revenue_a
    by_name = {bar.name: bar for bar in bridge.bars}
    rows: list[tuple[int, str, Fraction]] = []  # (indent, label, value)
    grouped: set[str] = set()
    for label, names in GROUPS:
        members = [by_name[name] for name in names if name in by_name]
        rows.append((0, label, _sum(bar.value for bar in members)))
        rows += [(1, bar.label, bar.value) for bar in members]
        grouped.update(names)
    rows += [(0, bar.label, bar.value) for bar in bridge.bars if bar.name not in grouped]

    start = f"Start: {bridge.year_a} net revenue"
    end = f"End: {bridge.year_b} net revenue"
    width = max(len(start), len(end), *(2 * indent + len(label) for indent, label, _ in rows))

    def line(indent: int, label: str, value: Fraction, signed: bool = True) -> str:
        text = f"  {'  ' * indent}{label}".ljust(width + 2)
        out = f"{text}  {_money(value, symbol, signed):>11}"
        return out + (f"  {float(value / base) * 100:+6.2f} pts" if signed else "")

    headline = (
        f"Net revenue, {bridge.currency}, {bridge.year_a} to {bridge.year_b}: "
        f"{_money(bridge.net_revenue_a, symbol)} to {_money(bridge.net_revenue_b, symbol)} "
        f"({float(bridge.change_pct):+.2%}, {_money(bridge.change, symbol, signed=True)})."
    )
    lines = [headline]
    if note := alignment_note(bridge):
        lines.append(note)
    lines += ["", line(0, start, base, signed=False)]
    lines += [line(indent, label, value) for indent, label, value in rows]
    lines += [line(0, end, bridge.net_revenue_b, signed=False), ""]
    lines.append(
        f"pts are percentage points of {bridge.year_a} net revenue: the unindented lines sum "
        f"to the {float(bridge.change_pct):+.2%} change, and each indented group sums to the "
        f"line above it."
    )
    largest = sorted(bridge.set_aside.items(), key=lambda kv: -abs(kv[1]))[:4]
    lines.append(
        f"Set aside, outside net revenue: the change in non-product lines, "
        f"{_money(bridge.non_product.value, symbol, signed=True)}, takes everything invoiced "
        f"from {_money(bridge.invoiced_a, symbol)} to {_money(bridge.invoiced_b, symbol)}. "
        "Largest changes by line type: "
        + ", ".join(f"{key} {_money(value, symbol, signed=True)}" for key, value in largest)
        + "."
    )
    lines.append(
        f"SKUs: {bridge.continuing_skus:,} sold in both years, {bridge.new_skus:,} new, "
        f"{bridge.discontinued_skus:,} discontinued. Volume, mix and price cover the sale "
        "lines of SKUs sold in both years, so volume is not the change in total units."
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("year_a", type=int)
    parser.add_argument("year_b", type=int)
    parser.add_argument("--currency", default="EUR", choices=sorted(AMOUNT_COLUMNS))
    args = parser.parse_args()
    # Read-only: fails while a build holds the file, by DuckDB's design.
    with duckdb.connect(warehouse_path(), read_only=True) as con:
        print(render(explain_change(con, args.year_a, args.year_b, args.currency)))


if __name__ == "__main__":
    main()
