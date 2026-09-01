# The published dashboard

### 👉 [ddscully.github.io/dlt-dbt-duckdb-evidence](https://ddscully.github.io/dlt-dbt-duckdb-evidence/)

Eleven pages, built from the modelled layers with [Evidence](https://evidence.dev/).
No year is hardcoded: every page reads the latest year each metric family can
actually populate from
[`reports/sources/warehouse/latest_years.sql`](../reports/sources/warehouse/latest_years.sql),
because coverage doesn't end in the same year for all of them.

Each page is declared as a dbt exposure in
[`dbt/models/_exposures.yml`](../dbt/models/_exposures.yml), so
`dbt ls --select +exposure:<name>` answers "what breaks if I change this" for one
page. Building the site locally is [`reports/README.md`](../reports/README.md);
what the pages are *for* is below.

## The pages

| Page | What's on it |
|------|--------------|
| **Home** | A routing page: pick the analysis that matches what you're responsible for. |
| **CBAM Exposure** | What a tonne of an imported CBAM good costs at the EU border, by where it was made: Annex I of Implementing Regulation (EU) 2025/2621, as corrected by (EU) 2026/1740, priced at a carbon price you choose. Semi-finished steel runs 63× from Azerbaijan to Indonesia, and the ranking sorts by *production route* rather than by the national grid, which is the opposite of the Scope 2 story. A screening tool, not a filing. |
| **Scope 2 Factors** | The same grid carbon-intensity series read as what it also is: the location-based Scope 2 emission factor a company multiplies its metered kWh by for a CSRD, SECR or CDP disclosure. `marts.dim_grid_emission_factors` as a reference table with its vintage and lineage, a worked example over twelve *invented* sites, and the three caveats a practitioner checks first. |
| **Retail Transactions** | One retailer's 1.07M invoice lines, the only page here below country grain. What counts as revenue when a negative quantity on a sale invoice is a stock write-off and not a return, cohort retention read as a triangle, what a customer's first order predicts about their lifetime value, RFM segmentation where SQL's `ntile` would split 3,227 customers away from their identical peers, and returns matched to their sale by inference. |
| **Currency** | The ECB's daily euro reference rates, and the three problems an annual warehouse never has to answer. 30% of calendar days carry no rate, so the daily table carries the last fixing forward, capped, because the two interior gaps in the series are the Icelandic króna after 2008 and the Argentine peso in 2002, not long weekends. Spot against average, and what it changes about a number already on the site: EU household electricity rose 35% or 13.5% from 2021-S1 to 2022-S2 depending only on whether you counted in euros or dollars. |
| **Weather** | Capital-city degree days as a control variable: "was it just a colder year" is the cheapest competing explanation for any energy or emissions movement, and this is the page that rules it in or out. Across 499 country-years of EU/EEA capitals the year-over-year change in heating demand explains 0.0% of the year-over-year change in household electricity price, and never more than 11.1% in any single year. Also: the two degree-day conventions disagreeing by up to 18.7%, the capital-as-proxy distance carried as a number, and why the current year is filtered out of every comparison. |
| **Eight Findings** | Eight write-ups on the joined data: when each country's emissions peaked, that the cleanup happened in electricity and coal is most of it, real-terms decoupling, whether it's just offshoring (it isn't, mostly), emissions tracking income rather than headcount, cumulative vs. current responsibility, carbon intensity falling while absolute tonnes rise, and the gap between the cleanest and dirtiest grids refusing to close. |
| **Country Explorer** | The same data with a year selector on it, for checking a specific country or year yourself instead of reading a conclusion. |
| **Coverage** | Which series actually cover which countries, by left-joining the fact onto the country-year spine so a gap is a row. Names both populations that break naive queries: territories with World Bank data and no OWID emissions, and countries with emissions and no World Bank GDP (Taiwan leads at 262 Mt, so it is silently absent from every intensity measure). |
| **Restatements** | Which CO₂ estimates OWID has revised since this warehouse first loaded them, off the dbt snapshot. |
| **Pipeline** | dlt load times per source, rows and year spans per layer, and all 447 dbt tests with their stored failure counts, from the observability tables that `transform/pipeline_status.py` writes. |

## How it is built and deployed

`.github/workflows/pages.yml` builds it as a single `publish_site` job. The site
is a node in the asset graph (`reports/evidence_site`), so the workflow
materializes it instead of running npm itself. It builds against the **live**
sources rather than the fixtures — a published dashboard showing the 17-country
test slice would be worse than none — weekly, on demand, and on any push to
`main` that touches something the site is built from. That last one is a
`paths:` allowlist rather than a `paths-ignore`, because `reports/pages/` is
markdown and ignoring markdown would stop republishing exactly when a page
changed.

Setting this up yourself takes three things nobody tells you about; they're in
[`reports/README.md`](../reports/README.md#deploying-to-github-pages).
