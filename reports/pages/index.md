---
title: What this warehouse answers
description: A working data warehouse covering carbon border costs, emission factors for disclosure, retail customer economics, currency effects, weather as a control variable and long-run emissions trends.
---

Six self-contained analyses built on public data. Each one ends in a decision
somebody has to make, and every figure is a live query, so the numbers move when
the underlying data does.

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

**Attributing an energy or emissions movement to anything at all**
→ **[Weather](/weather)** — whether it was simply a colder year, which is the
competing explanation that has to be ruled out first, and what happens when you
actually test it.

**Long-run emissions strategy, siting or supply agreements**
→ **[Eight Findings](/findings)** — what two decades of national emissions and
energy data support, and what they don't.

To check a specific country or year yourself rather than read a conclusion, use
the **[Country Explorer](/countries)**.

## What sits underneath

Seven public sources — emissions and energy from Our World in Data, development
indicators from the World Bank, electricity prices from Eurostat, exchange rates
from the European Central Bank, capital-city weather from Open-Meteo's ERA5
archive, the EU's own CBAM reference values, and one retailer's transaction log —
loaded, modelled, tested and published on a schedule.

Every table is released publicly each month as both Parquet and a DuckDB file.

## Before you quote a number

Two pages exist so the figures elsewhere can be taken at face value.
**[Coverage](/coverage)** says which countries and years each measure actually
covers, and where two sources disagree. Read it before cutting any chart to a
single latest year. **[Restatements](/restatements)** tracks figures that have
been revised since this warehouse first recorded them, because emissions data is
not a fixed record.

**[Pipeline](/pipeline)** reports the operational state of the load itself.

## What this project has and has not done yet

<Alert status=warning>

- **The engineering is built and measured**: ingestion, modelling, contracts,
  tests, lineage, orchestration, a publication boundary.
- **The analysis has not had the same scrutiny.** The pipeline does what it says;
  whether these are the right questions is untested. Treat the conclusions on
  these pages as illustrative, not as claims to rely on.
- **The Scope 2 worked example is fabricated data** over twelve invented sites,
  present so the model has something to demonstrate on. Every other figure on
  this site is a live query against a public source.
- **Coverage thins unevenly per column.** Several cross-source comparisons rest
  on it and no chart restates it, which is what
  **[Coverage](/coverage)** is for.

</Alert>

Why each of those, at length: [what this is not,
yet](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/blob/main/docs/FOR_REVIEWERS.md#0-what-this-is-not-yet).

---

<small>Sources: <a href="https://github.com/owid/co2-data">OWID CO₂</a>,
<a href="https://github.com/owid/energy-data">OWID Energy</a>,
<a href="https://databank.worldbank.org/source/world-development-indicators">World Bank WDI</a>,
<a href="https://ec.europa.eu/eurostat/databrowser/view/nrg_pc_204">Eurostat electricity prices</a>,
<a href="https://frankfurter.dev">ECB reference rates via Frankfurter</a>,
<a href="https://open-meteo.com/">Open-Meteo</a> (ERA5, Copernicus/ECMWF),
<a href="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=OJ%3AL_202502621">Implementing Regulation (EU) 2025/2621</a>,
<a href="https://archive.ics.uci.edu/dataset/502/online+retail+ii">UCI Online Retail II</a>.
Built with dlt, dbt, DuckDB, Polars, Dagster &amp; Evidence &mdash; every model,
test and workflow behind these numbers is on
<a href="https://github.com/Ddscully/dlt-dbt-duckdb-evidence">GitHub</a>, along
with a <a href="https://github.com/Ddscully/dlt-dbt-duckdb-evidence/releases/latest">monthly
release</a> of the whole warehouse as DuckDB and Parquet.</small>
