# A Complete Data Stack, Demonstrated End to End

*dlt → DuckLake → dbt → Polars → Evidence, orchestrated by Dagster. Everything
runs locally against one DuckDB file, rebuilt from live public sources on every
push.*

[![ci](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/actions/workflows/ci.yml/badge.svg)](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/actions/workflows/ci.yml)
[![nightly](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/actions/workflows/nightly.yml/badge.svg)](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/actions/workflows/nightly.yml)
[![pages](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/actions/workflows/pages.yml/badge.svg)](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/actions/workflows/pages.yml)
[![data snapshot](https://img.shields.io/github/v/release/Ddscully/dlt-dbt-duckdb-evidence?sort=date&filter=data-*&label=data%20snapshot&color=1f6feb)](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/releases/latest)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](./LICENSE)

**[📊 Live dashboard](https://ddscully.github.io/dlt-dbt-duckdb-evidence/)** ·
[Quickstart](#quickstart) ·
[Reuse this stack](#use-this-stack-for-your-own-data) ·
[Published data](#published-data) ·
[The practices, indexed](./docs/PRACTICES.md)

Seven public feeds go in; a dashboard and a queryable copy of the warehouse come
out. In between sit ingestion, modelling, contracts, orchestration and a
publication boundary, and none of them is a stub. No numbers are exported by
hand: the site rebuilds from the live sources on every push, so it is never more
than a week behind what the publishers release.

None of the wiring is specific to emissions. The same tree is packaged as a
[template](https://github.com/Ddscully/dlt-dbt-duckdb-template) with the subject
matter taken out, for pointing at your own data.

The stack is deliberately lightweight. Everything runs locally with `uv`: raw
lands as Parquet in a DuckLake catalog, and dbt builds into a single DuckDB file
— no cloud warehouse, no credentials, no bill.

```
dlt  ─▶  DuckLake  ─▶  dbt  ─▶  Polars  ─▶  Evidence
 EL     raw Parquet   stg/marts   heavy T     BI-as-code
└──────────────────── Dagster ─────────────────────┘
             one asset graph, scheduled
```

## What this demonstrates

- **Every layer has a worked example in it**, not a placeholder: dlt ingestion
  with incremental state and schema evolution, a DuckLake landing zone with its
  own snapshot lineage, 33 dbt models, two Polars transforms for what SQL models
  badly, and an Evidence site with a query behind every chart.
- **Dagster wraps the layers rather than replacing them**, so `ingest`, `dbt` and
  `transform` stay independently runnable. The asset graph is derived from keys
  the layers already share, not declared by hand.
- **518 tests — 482 data tests and 36 unit tests** — and an enforced schema
  contract on every mart model. The unit tests came out of a measurement: across
  five models mutated against a warehouse copy, 24 mutations were run and the
  data tests caught 3, so "nothing went red" is a finding here rather than an
  all-clear.
- **Two grains that are usually two separate projects**, because the modelling
  problems they pose are opposite: country-year facts, where the hard part is
  which country is missing from which series, and one wholesaler's 1.07M-line
  invoice log, where revenue depends on telling a stock write-off from a customer
  return when both are a negative quantity.
- **The warehouse itself is published, not only the dashboard** — monthly, with
  attribution per source. One column identifies a person; it is classified in the
  ymls and pseudonymised at the export, never in a model.
- **A red CI build means this repo broke**, not that a publisher was
  rate-limiting: every network call on a pull request is served from recorded
  fixtures. The nightly job is the one that hits the live endpoints, and it opens
  an issue when a source has moved.
- [`docs/PRACTICES.md`](./docs/PRACTICES.md) **indexes the practices with the
  failure each one prevents**, the number that measures it, and a link to where
  it happens in the code.

| Tool | Role |
|------|------|
| [**uv**](https://docs.astral.sh/uv/) | project & environment manager |
| [**dlt**](https://dlthub.com/) | EL: API/CSV ingestion into DuckLake w/ schema inference |
| [**DuckDB**](https://duckdb.org/) | in-process analytical warehouse: what dbt builds, in a single file |
| [**dbt**](https://docs.getdbt.com/) (`dbt-duckdb`) | T: staging + marts, tests, docs |
| [**Dagster**](https://dagster.io/) | orchestration: every layer as a software-defined asset |
| [**Polars**](https://pola.rs/) | heavy columnar transforms / window logic in Python |
| [**DuckLake**](https://ducklake.select/) | where `raw` lands: Parquet under a catalog in `data/lakehouse/`, with snapshot lineage you can diff |
| [**Evidence**](https://evidence.dev/) | BI-as-code dashboard, deployable to GitHub Pages |
| [**sqlfluff**](https://sqlfluff.com/) + pre-commit | SQL linting, in the hooks and in CI |
| [**pytest**](https://docs.pytest.org/) | unit tests over the ingest/transform logic |
| **GitHub Actions** | fixture-backed pipeline run on every PR, live run nightly |

---

> ### ⚠️ What this project has and has not done yet
>
> - **The engineering is built and measured**: ingestion, modelling, contracts,
>   tests, lineage, orchestration, a publication boundary. Judge that.
> - **The analysis has not had the same scrutiny.** The pipeline does what it
>   says; whether the questions are the right ones is untested. Treat the
>   conclusions as illustrative.
> - **`fct_example_scope2_emissions` is fabricated data** over twelve invented
>   sites, and it ships in the public release.
> - **Coverage thins unevenly per column.** Several cross-source comparisons rest
>   on it and no chart restates it.
>
> Why each of those, at length:
> [`docs/FOR_REVIEWERS.md` §0](./docs/FOR_REVIEWERS.md#0-what-this-is-not-yet).
> That document also answers the SLA, run-cost and what-breaks-at-1000×
> questions, and
> [scores the warehouse against a published governance rubric](./docs/FOR_REVIEWERS.md#6-scored-against-somebody-elses-rubric)
> it did not write — including the dimension it fails.

---

## Quickstart

Two things to install first. [uv](https://docs.astral.sh/uv/) manages Python and
every dependency here; `just` runs the recipes.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # or `brew install uv`
uv tool install rust-just                         # the `just` command runner
```

Then:

```bash
just setup      # uv sync runtime + dev + orchestration groups
just run        # ingest -> dbt build -> polars transform
just dagster    # ...or the same pipeline as an asset graph, UI on :3000
just sql        # poke around the warehouse in the DuckDB CLI
just report     # build the Evidence dashboard (needs Node ≥ 18)

# Tab-completion for recipe names, if you want it: `just --completions <shell>`
# https://just.systems/man/en/shell-completion-scripts.html
```

No credentials at any point; every source is a public endpoint. uv reads
`.python-version` and fetches CPython 3.13 itself if you haven't got it. The
whole asset graph *including* the site took **3 minutes** here from a cold cache,
and `just run` alone ≈ 65 s (per stage in
[`docs/FOR_REVIEWERS.md`](./docs/FOR_REVIEWERS.md) §3). Budget **~2.6 GB** on disk
once built, venv and `node_modules` included, all of it gitignored
and regenerable (`just clean`, or `just clean deep` to drop `node_modules` too).

Offline, or would rather not hit the public endpoints? `just test-pipeline` runs
the whole pipeline in ~46 s against recorded fixtures, into a throwaway
warehouse. `just course-sandbox` does the same into a warehouse that persists,
which is what the course exercises are built to break.

Prefer containers? `cp .env.example .env` (set `PGPASSWORD`), then
`just compose-build && just compose-up` runs the whole thing as four containers
— the graph, the dashboard behind nginx, Postgres holding the DuckLake catalog,
and SeaweedFS holding its Parquet — with each Dagster run getting its own
container. It is the recommended way to run this unattended;
[`docs/RUNNING_AS_A_SERVICE.md`](./docs/RUNNING_AS_A_SERVICE.md) §10 is the
runbook. Nothing above needs it: with no `.env`, everything stays on disk.

No `just`? The recipes map to plain commands; see the [`justfile`](./justfile).

## Use this stack for your own data

Most of what makes this repo work is not the emissions data: it is the layout,
the wiring conventions, the CI shape, and the package underneath that has no
domain in it.

[`dlt-dbt-duckdb-template`](https://github.com/Ddscully/dlt-dbt-duckdb-template)
is this repo cut back to a starting point — the same layers, wiring and CI, with
the subject matter, the publishing layer and the course removed, and one trivial
source (monthly gold prices) left in so `just test-pipeline` and the asset graph
are green from the first commit. `scripts/rename_project.py` renames the project
across every tracked file. It is a fork rather than a generated cut, so the two
trees drift by hand.

[`docs/REUSING_THIS_STACK.md`](./docs/REUSING_THIS_STACK.md) is what the template
cannot carry: what copies over unchanged, what has to be rewritten, the four
decisions that are expensive to revisit later, and the invariants that fail
silently. A clone followed it literally with an unrelated source until CI passed,
so it is corrected from that run rather than written from memory.

```
ingest/     dlt — one module per publisher, plus the pipeline's coordination
lake/       the DuckLake landing zone, where `raw` lives
dbt/        staging → intermediate → marts, with contracts, tests and groups
transform/  the Polars derived metrics, for what SQL models badly
orchestration/  the Dagster asset graph over all of the above
publish/    the boundary outward: the release, and the state it carries forward
reports/    the Evidence dashboard
src/modern_data_stack/   the domain-neutral mechanisms every layer calls
```

`src/modern_data_stack/` is the part with nothing domain-specific in it: it takes
its configuration as arguments, and the project modules that call it hold the
constants. Copy the directory, or depend on it and write only the layers above.

## The data it runs on

Seven public feeds, cleaned, joined on ISO code and year, and charted.

- **Country-year**, the figures organisations are required to act on: the grid
  carbon intensity behind every company's Scope 2 disclosure (30 g/kWh in Norway
  against 717 in South Africa, a 24× spread on the same kilowatt-hour), what a
  tonne of imported steel will cost at the EU border from 2026, and what
  electricity actually costs in each EU market. Here the hard part is *coverage*:
  which country is missing from which series, and what a join quietly drops.
- **Transaction grain**, the questions annual averages cannot ask: one UK
  wholesaler's complete 1.07M-line invoice log. Revenue depends on telling a
  stock write-off from a customer return when both are a negative quantity;
  returns are matched to their sale by inference, because nothing links them.
  It is also the only source with a person in it, which is why
  [`docs/DATA_PROTECTION.md`](./docs/DATA_PROTECTION.md) exists.

The feeds are [Our World in Data](https://github.com/owid/co2-data), the
[World Bank](https://databank.worldbank.org/source/world-development-indicators),
[Eurostat](https://ec.europa.eu/eurostat), the [ECB](https://frankfurter.dev),
[Open-Meteo](https://open-meteo.com/) and
[UCI's Online Retail II](https://archive.ics.uci.edu/dataset/502/online+retail+ii),
plus one EU regulatory annex that arrives as a seed.
[`docs/WAREHOUSE.md`](./docs/WAREHOUSE.md) has the grains and schemas.

## Orchestration

`just run` chains the steps in a shell; Dagster models the same pipeline as one
asset graph, which is what lets you rebuild only what a change touched.

```bash
just dagster                              # UI on :3000: graph, runs, freshness, checks
just materialize                          # whole graph, headless
just materialize-select 'raw/wb_wdi*'     # one source + everything downstream
just backfill-wdi 1990 1995               # re-load WDI for a range of years
```

Nothing declares the order by hand: dlt resource keys match the source keys
dagster-dbt derives from `_sources.yml`, the model edges come from dbt's own
`ref()` graph, and the site declares one dep per table its queries read. The
graph, the three jobs and the backfills are
[`docs/ORCHESTRATION.md`](./docs/ORCHESTRATION.md).

## Tests

```bash
just test           # pytest: mocked payloads, no network, ~47 s
just coverage       # the same, with line + branch coverage; gates nothing, ~58 s
just test-pipeline  # the whole pipeline against recorded fixtures, ~46 s
```

(Nothing checks a timing; `git log -S '<figure>'` finds the commit that
measured it.)

CI on a pull request runs both, plus the Dagster asset graph and the asset
checks, entirely offline — so a red build means *this repo* broke, not that a
publisher was rate-limiting. A nightly workflow runs the same graph against the
live endpoints and opens an issue when a source has moved, which is the cue to
`just record-fixtures`. Contributors should run `uv run pre-commit install` once;
CI runs the same hooks over every file. Details in
[`tests/README.md`](./tests/README.md).

Alongside them, `just dbt-build` runs 518 tests (482 data tests and 36 unit
tests) and enforces a schema contract on every mart model. What each gate catches
is [`docs/DATA_QUALITY.md`](./docs/DATA_QUALITY.md); why the gates are shaped
that way is [`docs/PRACTICES.md`](./docs/PRACTICES.md).

## Published dashboard

### [ddscully.github.io/dlt-dbt-duckdb-evidence](https://ddscully.github.io/dlt-dbt-duckdb-evidence/)

[![The Evidence dashboard: the Eight Findings page, with the site navigation, three headline figures and a scatter of the year each large emitter's CO₂ peaked](./docs/assets/dashboard.png)](https://ddscully.github.io/dlt-dbt-duckdb-evidence/findings)

Eleven pages built from the modelled layers, deployed by
`.github/workflows/pages.yml` as a single Dagster job. The site is a node in the
asset graph, so the workflow materializes it rather than running npm itself. It
builds against the **live** sources, because a published dashboard showing the
17-country test slice would be worse than none. What is on each page, and the
three deployment gotchas behind it, are in
[`docs/DASHBOARD.md`](./docs/DASHBOARD.md).

## Published data

### [Latest snapshot](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/releases/latest)

The dashboard is one consumer of the warehouse. The warehouse itself is published
monthly, so you can use the joined data without running any of this: the whole
DuckDB file, the DuckLake landing zone beside it, a Parquet per modelled table,
row counts and checksums. DuckDB will query it over HTTPS where it sits, without
downloading anything.

[`docs/PUBLISHED_DATA.md`](./docs/PUBLISHED_DATA.md) has the queries and the four
things worth knowing before you build on it.

## The docs

The README is the tour. The detail lives here:

| | |
|---|---|
| [`docs/PRACTICES.md`](./docs/PRACTICES.md) | **the practices this repo demonstrates, and where each one is in the code** |
| [`docs/WAREHOUSE.md`](./docs/WAREHOUSE.md) | the seven sources, their grains, the DuckLake landing zone and the schemas built from it |
| [`docs/ORCHESTRATION.md`](./docs/ORCHESTRATION.md) | the Dagster asset graph, the three jobs, backfills and freshness policies |
| [`docs/DATA_QUALITY.md`](./docs/DATA_QUALITY.md) | the 482 dbt tests, the mart-model contracts, and the groups, exposures and model versions around them |
| [`docs/DASHBOARD.md`](./docs/DASHBOARD.md) | the eleven dashboard pages, what each is for, and how the site is deployed |
| [`docs/PUBLISHED_DATA.md`](./docs/PUBLISHED_DATA.md) | the monthly data release and how to query it without cloning anything |
| [`docs/DATA_PROTECTION.md`](./docs/DATA_PROTECTION.md) | the one personal column: how it is classified, what the release does to it, and how identifiable a customer stays without it |
| [`docs/FOR_REVIEWERS.md`](./docs/FOR_REVIEWERS.md) | what is and is not finished, the SLA, run cost, what breaks at 1000×, and what I'd do differently |
| [`docs/REUSING_THIS_STACK.md`](./docs/REUSING_THIS_STACK.md) | what carries over to a different dataset, and the decisions that are expensive to revisit |
| [`docs/decisions/`](./docs/decisions/README.md) | why the repo works the way it does: each choice, and what was rejected |
| [`docs/RUNNING_AS_A_SERVICE.md`](./docs/RUNNING_AS_A_SERVICE.md) | `just serve` — the graph and the dashboard as one always-on service, why it is a `just` recipe and the container built on it, and the publish-and-swap design still to be built |

And [`docs/course/`](./docs/course/) teaches the same warehouse as material for
analytics engineers, built around the failures that stay green — a one-word join
edit that drops two thirds of the countries with all 482 tests still passing.
Modules 00–04 are written; 05–10 are outlined in the course index.

Plus [`docs/STYLE_GUIDE.md`](./docs/STYLE_GUIDE.md) for SQL conventions,
[`tests/README.md`](./tests/README.md) for the two test tiers, and
[`reports/README.md`](./reports/README.md) for the Evidence layer.

[`AGENTS.md`](./AGENTS.md) is the instructions file every coding agent reads, and
a directory-by-directory map of the repo worth reading as a human too. The
per-area detail sits in [`.agents/skills/`](./.agents/skills/), for the seams a
vendor skill cannot know about; `CLAUDE.md` adds Claude Code's plugins on top.

## License

Code is [MIT](./LICENSE). The data is not this project's to license: OWID's
[CO₂](https://github.com/owid/co2-data) and
[energy](https://github.com/owid/energy-data) datasets are CC BY 4.0, World Bank
WDI is CC BY 4.0, Eurostat data carries its own
[reuse policy](https://ec.europa.eu/eurostat/help/copyright-notice), the
CBAM default values are EU law, reusable under
[Decision 2011/833/EU](https://eur-lex.europa.eu/eli/dec/2011/833/oj), the
euro reference rates are the ECB's, under its
[reuse policy](https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html),
[UCI's Online Retail II](https://archive.ics.uci.edu/dataset/502/online+retail+ii)
(Chen, D., 2019) is CC BY 4.0, and the daily capital-city weather comes from
[Open-Meteo](https://open-meteo.com/) under CC BY 4.0, generated using Copernicus
Climate Change Service information (ECMWF ERA5).

Two licence decisions shaped the warehouse rather than just its paperwork: one
source left out entirely, and one whose data licence and API terms are different
documents. Both are in
[`docs/PRACTICES.md`](./docs/PRACTICES.md#6-the-boundary-outward).

Every one permits redistribution with attribution, which is what the data
releases rely on; each release ships an `ATTRIBUTION.md` naming the publisher
and licence per source. Attribute them, not this repo, for the numbers; the
joins and derived metrics are the only part that's ours. Nothing upstream is
redistributed in the repository *itself*: the pipeline fetches it at run time,
and the checked-in fixtures under `tests/fixtures/ingest/` are small excerpts
kept for offline testing.
