---
title: What this warehouse answers
description: A working data warehouse covering carbon border costs, emission factors for disclosure, retail customer economics, currency effects and long-run emissions trends.
---

Five self-contained analyses built on public data, each one ending in a decision
somebody has to make. Every figure on every page is a live query, so the numbers
move when the underlying data does.

Start with the page that matches what you are responsible for.

## If you are responsible for…

**Importing steel, cement, aluminium, fertiliser or hydrogen into the EU**
→ **[CBAM Exposure](/cbam)** — what a tonne costs at the border from each
sourcing country, and why supplier data is worth collecting.

**A CSRD, SECR or CDP filing**
→ **[Scope 2 Factors](/scope2)** — the grid emission factor for every country,
with its vintage and lineage, and a worked example of the disclosure line it
feeds.

**Customer revenue, retention or marketing spend**
→ **[Retail Transactions](/retail)** — where the revenue actually sits, who comes
back, and the three definitions that quietly change the answer.

**Reporting in more than one currency**
→ **[Currency](/currency)** — why the same price rose by very different amounts
depending on the currency you counted it in, and when to use an average rate
rather than a spot one.

**Long-run emissions strategy, siting or supply agreements**
→ **[Eight Findings](/findings)** — what two decades of national emissions and
energy data support, and what they don't.

To check a specific country or year yourself rather than read a conclusion, use
the **[Country Explorer](/countries)**.

## What sits underneath

Six public sources — emissions and energy from Our World in Data, development
indicators from the World Bank, electricity prices from Eurostat, exchange rates
from the European Central Bank, the EU's own CBAM reference values, and one
retailer's transaction log — loaded, modelled, tested and published on a
schedule.

Every table is released publicly each month as both Parquet and a DuckDB file.

## Before you quote a number

Two pages exist so that the figures elsewhere can be taken at face value.
**[Coverage](/coverage)** says which countries and years each measure actually
covers, and where two sources disagree — worth reading before cutting any chart
to a single latest year. **[Restatements](/restatements)** tracks figures that
have been revised since this warehouse first recorded them, because emissions
data is not a fixed record.

**[Pipeline](/pipeline)** reports the operational state of the load itself.

---

<small>Sources: <a href="https://github.com/owid/co2-data">OWID CO₂</a>,
<a href="https://github.com/owid/energy-data">OWID Energy</a>,
<a href="https://databank.worldbank.org/source/world-development-indicators">World Bank WDI</a>,
<a href="https://ec.europa.eu/eurostat/databrowser/view/nrg_pc_204">Eurostat electricity prices</a>,
<a href="https://frankfurter.dev">ECB reference rates via Frankfurter</a>,
<a href="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=OJ%3AL_202502621">Implementing Regulation (EU) 2025/2621</a>,
<a href="https://archive.ics.uci.edu/dataset/502/online+retail+ii">UCI Online Retail II</a>.
Built with dlt, dbt, DuckDB, Polars, Dagster &amp; Evidence.</small>
