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
npm run build     # static site -> build/ (deploy to GitHub Pages)
```

Or from the repo root: `just report`.

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
then `npm --prefix reports ci && npm --prefix reports run build`, and publish
`reports/build/`.
