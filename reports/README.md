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

## Chart colors

`evidence.config.yaml` sets a `theme.colorPalettes.default` and brand colors
(`primary`/`accent`/`base-100`/etc.) validated for both light and dark mode —
every pair clears the colorblind (protanopia/deuteranopia) and normal-vision
separation floors, unlike Evidence's stock palette, whose first few slots are
all near-indistinguishable blues.

Charts with a fixed, small set of categories pass an explicit `seriesColors`
map instead of relying on the global palette's implicit ordering, so a series
always gets the same color regardless of how the query happens to sort it:

- **`income_group`** (`stg_country`'s income ladder — High → Low, 4 categories)
  appears in `index.md`'s bubble/line/scatter charts. It's technically ordinal
  (High → Low is a ladder), which argues for a single-hue light→dark ramp —
  but with 4 overlapping categories on a scatter/bubble, adjacent ramp steps
  read as near-identical at a glance even though they clear the colorblind
  checks. Traded that for four fully distinct hues (blue/gold/magenta/green),
  which loses the "high-to-low" gradient reading but wins at-a-glance
  separation. This is the one series in the report where 4 categories share a
  chart that needs the *all-pairs* check (any two bubbles/points can be
  neighbors) rather than the cheaper *adjacent* check — this hue set is one of
  only two 4-color subsets of the documented 8 that clear all-pairs at all, and
  even it carries two WARN-tier flags (a light-mode contrast dip on gold/
  magenta, a dark-mode colorblind-separation dip on green/gold) that the
  legend + hover tooltip mitigate rather than eliminate. Don't push income_group
  past 4 categories without re-running the validator.
- **Binary progress/business-as-usual series** (`status`, `decoupled`,
  `direction` in `findings.md`) reuse the same two hues throughout: blue for
  the "decoupled" / past-peak / emissions-removed side, orange for the
  opposite. Deliberately not red/green — that pairing fails the colorblind
  check outright (ΔE ~4, well under the ~6 floor) despite looking fine to
  most readers.
- **`measure`** (CO₂ share vs. population share) isn't a good/bad pair, so it
  gets its own two hues (aqua/gold) rather than borrowing the progress pair.

Adding a new `series=` chart? Pick colors from the same validated set in
`evidence.config.yaml` rather than eyeballing new hex values, and if two
categories carry a value judgment (good/bad, on-track/off-track), avoid a
red/green pairing.

## Deploying to GitHub Pages

The site is fully static. In CI, run `just run` (to produce the DuckDB file),
then `npm --prefix reports ci`, `npm --prefix reports run sources:strict` and
`npm --prefix reports run build`, and publish `reports/build/`. That's what
`.github/workflows/pages.yml` does; `--strict` makes a missing or empty
warehouse fail the build rather than deploy an empty dashboard.
