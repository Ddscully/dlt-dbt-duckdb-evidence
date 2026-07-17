# Evidence dashboard

[Evidence.dev](https://evidence.dev) — "BI as code". SQL + markdown compile to a
static site, deployable free to GitHub Pages.

## Scaffold it

```bash
npx degit evidence-dev/template reports   # one-time, into this folder
cd reports && npm install
```

Point Evidence at the DuckDB warehouse in `evidence/connection.yaml`:

```yaml
name: warehouse
type: duckdb
options:
  filename: ../../data/warehouse.duckdb
```

## Pages (`pages/`)

Each `.md` page runs SQL against the marts and renders charts, e.g.:

- **CO₂ per \$ of GDP by income group over time** — `marts.fct_emissions_energy`
- **Renewables adoption vs. life expectancy** — join in a WDI mart
- **Carbon-efficiency ranking** — `analytics.co2_intensity` (Polars output)

```bash
npm run dev      # local preview
npm run build    # static site -> build/ (deploy to GitHub Pages)
```
