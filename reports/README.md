# Evidence dashboard

[Evidence.dev](https://evidence.dev) is "BI as code": SQL + markdown compile to a
static site, deployable free to GitHub Pages.

This folder is a working Evidence project wired to the repo's DuckDB warehouse.

## Prerequisites

Build the warehouse first (Evidence reads `../data/warehouse.duckdb`):

```bash
just run        # ingest -> dbt build -> polars transform
```

## Develop / build

```bash
cd reports
npm install       # first time
npm run dev       # local preview at http://localhost:3000
npm run sources   # extract the warehouse tables -> .evidence/ parquet
npm run build     # static site -> build/ (deploy to GitHub Pages)
```

Or from the repo root: `just report` (which runs both).

**`npm run build` does not run the sources.** It renders against whatever
parquet `.evidence/` already holds, and `.evidence/` is gitignored — so a build
from a fresh clone succeeds and produces a site where every chart reads
*Table with name emissions_energy does not exist*. `npm run dev` extracts them
for you, which is why this only ever bites in CI.

## How it's wired

- `sources/warehouse/connection.yaml`: DuckDB source pointing at the warehouse file.
- `sources/warehouse/*.sql`: source queries that read the dbt/Polars outputs
  (`marts.fct_emissions_energy`, `analytics.co2_intensity`). Referenced in pages
  as `warehouse.emissions_energy`, `warehouse.co2_intensity`.
- `pages/index.md`: the dashboard, with renewables vs. life expectancy (bubble),
  CO₂ intensity by income group over time (line), and a most-efficient table,
  all driven by a year selector.
- `pages/findings.md`: five written-up findings from the same two tables.

## Pages (`pages/`)

Add a `.md` file per page; each runs SQL against the `warehouse` source and
renders charts. See the [Evidence docs](https://docs.evidence.dev) for components.

## Deploying to GitHub Pages

The site is fully static. In CI, run `just run` (to produce the DuckDB file),
then `npm --prefix reports ci`, `npm --prefix reports run sources:strict` and
`npm --prefix reports run build`, and publish `reports/build/`. That's what
`.github/workflows/pages.yml` does; `--strict` makes a missing or empty
warehouse fail the build rather than deploy an empty dashboard.
