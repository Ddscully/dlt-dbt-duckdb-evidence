# 0013. Two years of different coverage are compared over the window both cover, and the answer says so

Status: accepted 2026-09-28 (#113)

## Context

The retail extract runs from 2009-12-01 to 2011-12-09, so no two of its years
cover the same days: 2009 is one December, 2011 stops 22 days short. The
comparison anyone asks first, 2010 against 2011, reads as a fall that is partly
missing days. Measured on net revenue (`agent/bridge.py`, warehouse built
2026-09-28):

| 2010 → 2011 | GBP | EUR |
|---|---|---|
| full calendar years | −5.62% | −6.86% |
| both over 1 Jan–9 Dec | **−1.95%** | **−3.19%** |
| 2011 pro-rated by 365/343 | +0.43% | |

The last day each year *trades* is not where its data ends: 2010's last sale is
23 Dec, because the business closes over New Year.

## Decision

`explain_change` compares both years over the month-day window both are
covered for, taken from the fact's first and last `invoice_date` — 1 Jan–9 Dec
for 2010 against 2011, 1–31 Dec for 2009 against 2010. Whenever the window is
not the full year, the answer states it, the day counts, and what the
full-year comparison would have read.

## Rejected

- **Refusing the comparison.** It is the first question a finance reader asks
  of this data, and a refusal sends them to compute the naive figure
  themselves, which overstates the fall by more than half.
- **Pro-rating the short year** by its share of days. It flips the sign: 2011
  scaled up reads +0.43% where the same days read −1.95%. Pro-rating assumes
  the missing 10–31 December trade at the year's average, and it gives those
  22 days £579k where the same days of 2010 took £358k: the shop closes over
  Christmas.
- **Each year's own first and last sale as its coverage.** In this extract it
  gives the same window, because 2010 is the only complete year, but it reads a
  trading closure as missing data: two complete years that closed on 22 and 23
  December would each lose a real trading day, and the answer would warn about
  a gap that is not there.

## Consequences

An aligned answer never covers a full year when the extract's ends are in play,
so its totals differ from a plain `sum` over `year`; the note is what tells the
reader why. A leap day is a one-day difference the window does not correct; the
note's day counts show it. Periods finer than a year would need the same rule
per period, and it would be worth revisiting if the extract were ever extended.
