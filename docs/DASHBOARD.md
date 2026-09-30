# The published dashboard

### 👉 [ddscully.github.io/dlt-dbt-duckdb-evidence](https://ddscully.github.io/dlt-dbt-duckdb-evidence/)

- **Eleven top-level pages**, each analysis page an overview that leads with
  its charts and links down to detail pages carrying the argument, the tables
  and the method ([The pages](#the-pages)).
- **No page hardcodes a year**: each reads the latest year its metric family can
  actually populate
  ([`reports/README.md`](../reports/README.md#no-hardcoded-years)).
- **Every page that reads a model is a dbt exposure**, so
  `dbt ls --select +exposure:<name>` answers "what breaks if I change this" for
  one page ([Beside the pages](#beside-the-pages)).
- **dbt's docs ship at `/dbt/` as the data catalogue**, with dbt's visitor
  tracking switched off ([Beside the pages](#beside-the-pages)).
- **The site is rebuilt from the live sources** weekly, on demand and on any
  push that touches what it is built from
  ([How it is built and deployed](#how-it-is-built-and-deployed)).

## The pages

| Page | What is on it |
|------|--------------|
| **Home** | A world map of CO₂ per person, then one small chart per analysis, each linking to its page. |
| **CBAM Exposure** | What a tonne of an imported CBAM good costs at the EU border before any deduction, by where it was made: Annex I of Implementing Regulation (EU) 2025/2621, as corrected by (EU) 2026/1740, priced at a carbon price you choose. Semi-finished steel carries a gross cost of about €11 a tonne from Azerbaijan and €677 from Indonesia, and the ranking sorts by *production route* rather than by the national grid, which is the opposite of the Scope 2 story. Primary aluminium shows it most plainly: across every country with a value, the gross border cost is uncorrelated with the grid the metal was smelted on. A screening tool, not a filing: the figures come before the deductions for EU ETS free allocation and for a carbon price paid where the good was made. |
| **Scope 2 Factors** | The same grid carbon-intensity series read as a stand-in for the location-based Scope 2 emission factor a company multiplies its metered kWh by for a CSRD, SECR or CDP disclosure: a grid average like that factor, but a lifecycle one in CO₂e. `marts.dim_grid_emission_factors` as a reference table with its vintage and lineage, a worked example over twelve *invented* sites, and the four caveats a practitioner checks first. |
| **Retail Transactions** | One retailer's 1.07M invoice lines, the only page here below country grain. What counts as revenue when a negative quantity on a sale invoice is a stock write-off and not a return, cohort retention read as a triangle, what a customer's first order predicts about their lifetime value, RFM segmentation where SQL's `ntile` would split 3,227 customers away from their identical peers, and returns matched to their sale by inference. |
| **Currency** | The ECB's daily euro reference rates, and the three problems an annual warehouse never has to answer. 30% of calendar days carry no rate, so the daily table carries the last fixing forward, capped, because the two interior gaps in the series are the Icelandic króna after 2008 and the Argentine peso in 2002, not long weekends. Spot against average, and what it changes about a number already on the site: EU household electricity rose about 36% or 14% from 2021-S1 to 2022-S2 depending only on whether you counted in euros or dollars. |
| **Weather** | Capital-city degree days as a control variable: "was it just a colder year" is the cheapest competing explanation for any energy or emissions movement, and this is the page that rules it in or out. Across 499 country-years of European capitals the year-over-year change in heating demand explains 0.0% of the year-over-year change in household electricity price, and never more than 11.1% in any single year. Tested against what the countries emitted instead, it explains about half of the panel's year-to-year swing in CO₂, pandemic years left out, and the fit is strongest where winters are cold and heating burns gas. Also: the two degree-day conventions disagreeing by up to 18.7%, the capital-as-proxy distance carried as a number, and why the current year is filtered out of every comparison. |
| **Nine Findings** | One chart and a caption per finding, each linking to a page of its own with the argument, the data and the decision it feeds: when each country's emissions peaked, that the cleanup happened in electricity and coal is most of it, real-terms decoupling, whether it's just offshoring (it isn't, mostly), emissions tracking income rather than headcount, cumulative vs. current responsibility, carbon intensity falling while absolute tonnes rise, the gap between the cleanest and dirtiest grids refusing to close, and the rich world's net cut since 2005 being coal, which leaves oil and gas. |
| **Country Explorer** | The same data with a year selector and a country selector on it, for checking a specific country or year yourself instead of reading a conclusion: a world map of grid intensity for the chosen year, and one country's CO₂ per person and grid intensity since 1990. Its income-group trend groups each country as the World Bank classified it that year and weights by output, beside the different ranking that today's groups and a plain country average give. |
| **Coverage** | Which series actually cover which countries, by left-joining the fact onto the country-year spine so a gap is a row. Names both populations that break naive queries: territories with World Bank data and no OWID emissions, and countries with emissions and no World Bank GDP (Taiwan leads at 262 Mt, so it is silently absent from every intensity measure). |
| **Restatements** | Which CO₂ estimates OWID has revised since this warehouse first loaded them, off the dbt snapshot. |
| **Pipeline** | dlt load times per source, rows and year spans per layer, and every data test with its stored failure count, from the observability tables that `transform/pipeline_status.py` writes. |

## Beside the pages

Each page that reads a model is declared as a dbt exposure in
[`dbt/models/_exposures.yml`](../dbt/models/_exposures.yml). Building the site
locally is [`reports/README.md`](../reports/README.md).

The same site carries dbt's docs at
[`/dbt/`](https://ddscully.github.io/dlt-dbt-duckdb-evidence/dbt/), linked from
Home as the data catalogue: every model and column with its description,
contract, tests and lineage, without a clone. `publish/build_report.py` writes
them as one self-contained file (`dbt docs generate --static`) after Evidence
builds, with dbt's visitor tracking switched off — left at dbt's default, the
page loads a Snowplow tracker for everyone who opens it.

## How it is built and deployed

`.github/workflows/pages.yml` builds it by materializing one Dagster job,
`publish_site`, and deploys the result. The site is a node in the asset graph (`reports/evidence_site`), so the workflow
materializes it instead of running npm itself. It builds against the **live**
sources rather than the fixtures — a published dashboard showing the 17-country
slice the tests run on would be worse than none — weekly, on demand, and on any push to
`main` that touches something the site is built from. That last one is a
`paths:` allowlist rather than a `paths-ignore`, because `reports/pages/` is
markdown and ignoring markdown would stop republishing exactly when a page
changed.

Setting this up yourself takes three steps that are easy to miss; they are in
[`reports/README.md`](../reports/README.md#deploying-to-github-pages).
