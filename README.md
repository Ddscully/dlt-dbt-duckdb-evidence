# A Lightweight Data Stack, Demonstrated End to End

A complete data platform that runs on a laptop. Public data is loaded with dlt
into a DuckLake landing zone, modelled with dbt into a single DuckDB file,
extended with Polars, and published as an Evidence dashboard and a monthly data
release, all orchestrated as one Dagster asset graph. No cloud account, no
credentials, no bill.

[![ci](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/actions/workflows/ci.yml/badge.svg)](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/actions/workflows/ci.yml)
[![nightly](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/actions/workflows/nightly.yml/badge.svg)](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/actions/workflows/nightly.yml)
[![pages](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/actions/workflows/pages.yml/badge.svg)](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/actions/workflows/pages.yml)
[![data snapshot](https://img.shields.io/github/v/release/Ddscully/dlt-dbt-duckdb-evidence?sort=date&filter=data-*&label=data%20snapshot&color=1f6feb)](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/releases/latest)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](./LICENSE)

**[📊 Live dashboard](https://ddscully.github.io/dlt-dbt-duckdb-evidence/)** ·
[Data catalogue](https://ddscully.github.io/dlt-dbt-duckdb-evidence/dbt/) ·
[Latest data release](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/releases/latest) ·
[Quickstart](#quickstart) ·
[Reuse this stack](#reuse-this-stack) ·
[The practices, indexed](./docs/PRACTICES.md) ·
[What is not done yet](#what-this-project-has-and-has-not-done-yet)

**518 dbt tests** (482 data, 36 unit) · an enforced schema contract on every
mart model · the whole pipeline run offline on every pull request · the site
rebuilt from live sources weekly

```
dlt  ─▶  DuckLake  ─▶  dbt  ─▶  Polars  ─▶  Evidence
 EL     raw Parquet   stg/marts   heavy T     BI-as-code
└──────────────────── Dagster ─────────────────────┘
             one asset graph, scheduled
```

## Why look at it

- **Every layer does real work.** Seven public feeds go in: emissions and energy
  from Our World in Data, development indicators from the World Bank, electricity
  prices from Eurostat, exchange rates from the ECB, weather from Open-Meteo and
  one retailer's invoice log, plus an EU regulation transcribed as a seed. A
  dashboard and a queryable warehouse come out, and no number is exported by
  hand.
- **Two grains with opposite hard parts.** For the country-year facts the hard
  part is coverage: which country is missing from which series, and what a join
  quietly drops. For the 1.07M-line transaction log it is definitions: a customer
  return and a stock write-off are both a negative quantity.
- **The tests were measured, not assumed.** When seven models were deliberately
  broken 38 ways, the data tests caught 5. The unit tests exist because of that
  gap.
- **CI does not depend on anyone else's API.** Every pull request runs the whole
  pipeline against recorded fixtures, and a nightly job runs it against the live
  sources and opens an issue when one changes.
- **The warehouse is published, not just the dashboard**: monthly, as DuckDB and
  Parquet, with attribution per source and the one column that identifies a
  person pseudonymised on the way out.
- **Dagster wraps the layers instead of replacing them.** `ingest`, `dbt` and
  `transform` still run on their own, and the asset graph comes from keys the
  layers already share.

## Quickstart

**Nothing installed?**
[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/Ddscully/dlt-dbt-duckdb-evidence)
opens the repo in a browser with everything installed and `just setup` already
run. [`.devcontainer/`](./.devcontainer/) does the same in VS Code.

Locally you need [uv](https://docs.astral.sh/uv/), which manages Python and every
dependency, and [`just`](https://just.systems/), which runs the recipes:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # or: brew install uv
uv tool install rust-just

git clone https://github.com/Ddscully/dlt-dbt-duckdb-evidence.git
cd dlt-dbt-duckdb-evidence

just setup      # create the Python environment
just run        # ingest -> dbt build -> Polars, about a minute
just report     # build the dashboard (needs Node 18+)
just sql        # query the warehouse in the DuckDB CLI
just dagster    # the same pipeline as an asset graph, UI on :3000
```

`just` on its own lists every recipe. A built tree takes about 2.6 GB, all of it
gitignored; `just clean` reclaims the build output, and `just clean deep` the
`node_modules` too.

- **Offline:** `just test-pipeline` runs the whole pipeline against recorded
  fixtures in under a minute, into a throwaway warehouse.
- **As a service:** `cp .env.example .env`, set `PGPASSWORD`, then
  `just compose-build && just compose-up` runs everything as containers, with
  the lake's catalog in Postgres and its Parquet in S3-compatible storage
  ([runbook](./docs/RUNNING_AS_A_SERVICE.md)).
- **Contributing:** `uv run pre-commit install` once; CI runs the same hooks.
  The test tiers are in [`tests/README.md`](./tests/README.md).

## What is in the repo

| Directory | Tool | Role |
|-----------|------|------|
| `ingest/` | [dlt](https://dlthub.com/) | one module per publisher: incremental loads with schema inference |
| `lake/` | [DuckLake](https://ducklake.select/) | the landing zone for raw data: Parquet under a catalog, with snapshot history |
| `dbt/` | [dbt](https://docs.getdbt.com/) + [DuckDB](https://duckdb.org/) | staging → intermediate → marts in one file, with contracts and tests |
| `transform/` | [Polars](https://pola.rs/) | the metrics SQL models badly |
| `orchestration/` | [Dagster](https://dagster.io/) | one asset graph over every layer ([how](./docs/ORCHESTRATION.md)) |
| `reports/` | [Evidence](https://evidence.dev/) | the dashboard, as SQL and markdown |
| `publish/` | | the boundary outward: the site build, the monthly release and the history it carries forward |
| `src/modern_data_stack/` | | the domain-neutral code every layer calls |

## The dashboard and the data

[![The Evidence dashboard: the Findings page, with the site navigation, three headline figures and a scatter of the year each large emitter's CO₂ peaked](./docs/assets/dashboard.png)](https://ddscully.github.io/dlt-dbt-duckdb-evidence/findings)

The [dashboard](https://ddscully.github.io/dlt-dbt-duckdb-evidence/) holds six
analyses, from CBAM border costs to retail customer retention, each ending in a
decision somebody has to make, plus a country explorer and pages on coverage,
restatements and the pipeline itself. dbt's [model documentation](https://ddscully.github.io/dlt-dbt-duckdb-evidence/dbt/)
sits beside it. [`docs/DASHBOARD.md`](./docs/DASHBOARD.md) describes every page
and how the site is deployed.

The [monthly data release](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/releases/latest)
is the whole warehouse as a DuckDB file and a Parquet per table, which DuckDB can
query over HTTPS without downloading it.
[`docs/PUBLISHED_DATA.md`](./docs/PUBLISHED_DATA.md) has example queries.

## Reuse this stack

Almost none of the wiring is specific to emissions data.
[`dlt-dbt-duckdb-template`](https://github.com/Ddscully/dlt-dbt-duckdb-template)
is this repo with the subject matter taken out and one trivial source left in,
green in CI from the first commit.
[`docs/REUSING_THIS_STACK.md`](./docs/REUSING_THIS_STACK.md) covers what the
template cannot: what has to be rewritten, the decisions that are expensive to
change later, and the invariants that fail silently.

## Documentation

| Doc | What it covers |
|-----|----------------|
| [`docs/PRACTICES.md`](./docs/PRACTICES.md) | **the practices this repo demonstrates, the failure each prevents, and where it is in the code** |
| [`docs/FOR_REVIEWERS.md`](./docs/FOR_REVIEWERS.md) | what is unfinished, the freshness SLA, run cost, what breaks at 1000×, and what I would change |
| [`docs/WAREHOUSE.md`](./docs/WAREHOUSE.md) | the sources, their grains, the landing zone and the schemas |
| [`docs/ORCHESTRATION.md`](./docs/ORCHESTRATION.md) | the asset graph, its jobs, backfills and freshness policies |
| [`docs/DATA_QUALITY.md`](./docs/DATA_QUALITY.md) | tests, contracts, groups, exposures and model versions |
| [`docs/DASHBOARD.md`](./docs/DASHBOARD.md) | each dashboard page and how the site is deployed |
| [`docs/PUBLISHED_DATA.md`](./docs/PUBLISHED_DATA.md) | the monthly release and how to query it |
| [`docs/DATA_PROTECTION.md`](./docs/DATA_PROTECTION.md) | the one personal column and what the release does to it |
| [`docs/REUSING_THIS_STACK.md`](./docs/REUSING_THIS_STACK.md) | adapting the stack to a different dataset |
| [`docs/RUNNING_AS_A_SERVICE.md`](./docs/RUNNING_AS_A_SERVICE.md) | running the graph and dashboard as an always-on service |
| [`docs/decisions/`](./docs/decisions/README.md) | why each design choice was made, and what was rejected |

[`docs/course/`](./docs/course/) teaches the warehouse to analytics engineers
through failures that pass every test, such as a one-word join edit that drops
two thirds of the countries. Modules 00–04 are written.
[`docs/STYLE_GUIDE.md`](./docs/STYLE_GUIDE.md) has the SQL conventions,
[`reports/README.md`](./reports/README.md) the Evidence layer, and
[`AGENTS.md`](./AGENTS.md), the guide every coding agent reads, is a map of the
repo worth reading as a human too.

## What this project has and has not done yet

- **The engineering is built and measured**: ingestion, modelling, contracts,
  tests, lineage, orchestration and the publication boundary.
- **The analysis has not had the same scrutiny.** The pipeline does what it
  says, but whether it asks the right questions is untested, so treat the
  conclusions as illustrative.
- **`fct_example_scope2_emissions` is fabricated**: twelve invented sites,
  shipped in the public release so the Scope 2 model has something to work on.
- **Coverage thins unevenly across columns**, so cross-source comparisons are
  often made over different sets of countries.

[`docs/FOR_REVIEWERS.md`](./docs/FOR_REVIEWERS.md#0-what-this-is-not-yet)
explains each of these, and
[scores the warehouse against a governance rubric](./docs/FOR_REVIEWERS.md#6-scored-against-somebody-elses-rubric)
it did not write, including the part where it fails.

## License

Code is [MIT](./LICENSE). The data belongs to its publishers:

| Source | Licence |
|--------|---------|
| OWID's [CO₂](https://github.com/owid/co2-data) and [energy](https://github.com/owid/energy-data) datasets | CC BY 4.0 |
| World Bank WDI | CC BY 4.0 |
| Eurostat | its own [reuse policy](https://ec.europa.eu/eurostat/help/copyright-notice) |
| CBAM default values | EU law, reusable under [Decision 2011/833/EU](https://eur-lex.europa.eu/eli/dec/2011/833/oj) |
| ECB euro reference rates | the ECB's [reuse policy](https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html) |
| [UCI's Online Retail II](https://archive.ics.uci.edu/dataset/502/online+retail+ii) (Chen, D., 2019) | CC BY 4.0 |
| Daily capital-city weather from [Open-Meteo](https://open-meteo.com/) | CC BY 4.0, generated using Copernicus Climate Change Service information (ECMWF ERA5) |

Each permits redistribution with attribution, and every data release ships an
`ATTRIBUTION.md` crediting them per source; cite the publishers for the numbers.
The repository stores little of it: dbt seeds transcribed from the CBAM
regulation and the World Bank's historical income classifications, and small
test fixtures. Everything else is fetched at run time. Two licence decisions
that shaped the warehouse are in
[`docs/PRACTICES.md`](./docs/PRACTICES.md#6-the-boundary-outward).
