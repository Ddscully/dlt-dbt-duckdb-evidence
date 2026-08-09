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

Or from the repo root: `just report` (which runs all three), `just report-clean`
to drop the schema cache first.

**`npm run build` does not run the sources.** It renders against whatever
parquet `.evidence/` already holds, and `.evidence/` is gitignored, so a build
from a fresh clone succeeds and produces a site where every chart reads
*Table with name emissions_energy does not exist*. `npm run dev` extracts them
for you, which is why this only ever bites in CI.

## The site is a Dagster asset

`reports/evidence_site` (in `orchestration/assets.py`) builds this folder, so the
dashboard is the last node of the asset graph rather than something built beside
it. `scripts/build_report.py` is the implementation, the same one `just report`
calls, so the recipe and the graph can't drift into running different builds.

- It declares **one dep per table the source queries read** (eight assets, ten
  tables), and `tests/test_report.py` fails if a new source query reads a table
  none of them covers. Adding `sources/warehouse/foo.sql` on a new mart therefore
  means adding a line to `TABLE_TO_DBT_MODEL` in `scripts/build_report.py`; see
  `just test`'s failure message, which says exactly which table is unclaimed.
- It is the only asset **excluded from the `full_refresh` job**, because it needs
  Node and three workflows run that job on a Python-only checkout.
  `just materialize-site` (`publish_site`) is the one that includes it.
- A blocking asset check, `site_pages_all_rendered`, asserts every `pages/*.md`
  produced HTML, because `evidence build` exits 0 for a site missing a page.

## How it's wired

- `sources/warehouse/connection.yaml`: DuckDB source pointing at the warehouse file.
- `sources/warehouse/*.sql`: source queries that read the dbt/Polars outputs
  (`marts.fct_emissions_energy`, `analytics.co2_intensity`,
  `marts.fct_eu_electricity_prices_semiannual`, the spine, the snapshot summary
  and the `pipeline_*` tables). Referenced in pages as
  `warehouse.emissions_energy`, `warehouse.co2_intensity`,
  `warehouse.eu_electricity_prices_semiannual`, and so on: the filename is the
  reference name.
- `pages/index.md`: the home page, an interactive explorer driven by a year
  selector: clean electricity vs. life expectancy (bubble), CO₂ intensity by
  income group over time (line), a grid carbon-intensity ranking and a
  most-efficient table. Its last section is the exception: EU prices half by
  half, from the semi-annual fact, deliberately covering the whole series rather
  than the selected year, because the annual average the other charts use hides
  moves of 300%+ inside a single year.
- `pages/findings.md`: seven written-up findings from the same two tables. Each
  section leads with its chart and puts the reading of it underneath; the notes
  on method sit at the bottom of the page.
- `pages/coverage.md`: what each source actually covers, built by left-joining
  `marts.fct_emissions_energy` onto the `dim_country_year` spine so a gap is a
  row rather than an absence. Read this before writing a `where` clause against
  the mart, since the 79/210/217-country ceilings live here.
- `pages/pipeline.md`: the state of the pipeline itself, with dlt load times,
  rows per layer, and every dbt test with its stored-failure count. Reads the
  three `analytics.pipeline_*` tables written by `transform/pipeline_status.py`
  (`just pipeline-status`, part of `just run`).
- `pages/restatements.md`: what OWID has revised since this warehouse first
  loaded it, off the dbt snapshot.
- `pages/scope2.md`: the same grid carbon-intensity series the other pages chart,
  read as the location-based Scope 2 emission factor it also is —
  `marts.dim_grid_emission_factors` as a reference table with its vintage and
  lineage, a worked example over twelve *invented* sites
  (`marts.fct_example_scope2_emissions`), and the three caveats a practitioner
  checks. It is the one page whose data is partly fabricated, which is stated in
  an `<Alert>` directly above the table rather than in a footnote.

The coverage and pipeline pages render an explanatory branch rather than an
error when their data is empty, the way `restatements.md` does, because the
published copy is always built from scratch.

## No hardcoded years

`sources/warehouse/latest_years.sql` returns one row holding the latest year each
metric family can populate, and every `where year = …` in the pages reads from
it. Pages pick it up with a page-level query block, which is what makes it
referenceable in both SQL and markdown:

```sql
select * from warehouse.latest_years
```

...then `(select co2_year from ${latest_years})` inside a query.

**It is not `max(year)`, and the difference is the whole point.** The mart sits on
a country-year spine, so its max year is whichever source runs furthest ahead:
Eurostat prices, currently a year beyond everything else. Coverage also falls off
at different rates per column: `primary_energy_twh` drops from ~210 countries to
79 in the latest year, while `co2_mt` holds at 214. Cutting an energy chart to the
latest CO₂ year silently discards two thirds of its sample. Each family therefore
gets its own floor; see the comment block at the top of that file.

Adding a chart on a new column? Check its coverage curve first
(`select year, count(col) from marts.fct_emissions_energy group by year`), and add
a family to `latest_years.sql` if the existing ones don't fit.

## Pages (`pages/`)

Add a `.md` file per page; each runs SQL against the `warehouse` source and
renders charts. See the [Evidence docs](https://docs.evidence.dev) for components.

## Reserved column names in charts

**Never name a charted column `tests` or `rows`.** They collide with Evidence's
chart internals, and the failure is silent and convincing: the BarChart renders
its axis, its categories and its value labels correctly, and simply draws no
bars. No console error, no build failure, no empty-data warning. The chart just
looks like every value is zero while the labels next to it say otherwise.

`pipeline.md` hit this twice. Renaming to `n_tests` fixed it with no other
change. `DataTable` is unaffected, so a column can be fine in a table on the same
page and barless in a chart three lines below it. If a chart renders labels but
no marks, rename the column before debugging anything else.

**One false alarm worth knowing about:** a headless screenshot with too small a
`--virtual-time-budget` produces the *identical* symptom — axis, categories and
value labels, no bars — and does it to a different chart on each run, because the
budget expires part-way through rendering a long page. `scope2.md` has three
charts and 25 s left one of them blank each time, varying. 60 s renders all three,
repeatably. Before believing a chart is broken, shoot it twice: a real failure is
the same chart every time.

## Years render as `2025.0` unless you cast twice

**Evidence's DuckDB extractor writes every numeric column to parquet as
`DOUBLE`**, whatever it was in the warehouse. Page queries run in the browser
against *that*, so `cast(year as varchar)` in a page produces `'2025.0'` — a
string, so no `fmt` can rescue it, and it lands in the BigValue, the DataTable
cell and the chart's category axis looking like a bug in the data.

Two fixes, and which one depends on where the value goes:

- **A number rendered as a number** (DataTable column, BigValue): leave the year
  numeric and pass `fmt="0"`.
- **A number that has to be a string** (a chart's category axis, a label
  concatenated into text): `cast(cast(year as integer) as varchar)`. The integer
  cast is the load-bearing half.

`latest_years.sql` sidesteps it by casting in the *source* query, which runs
server-side before the extraction — hence its `*_label` columns. Anything
computed on a page has to do it the long way; `scope2.md` has both forms with the
reason inline.

## Chart colors

`evidence.config.yaml` sets a `theme.colorPalettes.default` and brand colors
(`primary`/`accent`/`base-100`/etc.) validated for both light and dark mode.
Every pair clears the colorblind (protanopia/deuteranopia) and normal-vision
separation floors, unlike Evidence's stock palette, whose first few slots are
all near-indistinguishable blues.

Charts with a fixed, small set of categories pass an explicit `seriesColors`
map instead of relying on the global palette's implicit ordering, so a series
always gets the same color regardless of how the query happens to sort it:

- **`income_group`** (`stg_country`'s income ladder, High → Low, 4 categories)
  appears in `index.md`'s bubble/line/scatter charts. It's technically ordinal
  (High → Low is a ladder), which argues for a single-hue light→dark ramp,
  but with 4 overlapping categories on a scatter/bubble, adjacent ramp steps
  read as near-identical at a glance even though they clear the colorblind
  checks. Traded that for four fully distinct hues (blue/gold/magenta/green),
  which loses the "high-to-low" gradient reading but wins at-a-glance
  separation. This is the one series in the report where 4 categories share a
  chart that needs the *all-pairs* check (any two bubbles/points can be
  neighbors) rather than the cheaper *adjacent* check. This hue set is one of
  only two 4-color subsets of the documented 8 that clear all-pairs at all, and
  even it carries two WARN-tier flags (a light-mode contrast dip on gold/
  magenta, a dark-mode colorblind-separation dip on green/gold) that the
  legend + hover tooltip mitigate rather than eliminate. Don't push income_group
  past 4 categories without re-running the validator.
- **`income_group` also drives the peak-emissions scatter in `findings.md`**, but
  only the three categories present among large emitters that have already
  peaked (High/Upper-middle/Lower-middle, since no Low-income country clears the
  200 Mt threshold). That's a different `seriesColors` map from `index.md`'s
  four-category one: the first three palette slots (blue/orange/aqua), which
  are the ones that clear the *all-pairs* check on their own, with no need for
  `index.md`'s special-cased four-hue set. Still-rising countries are
  excluded from that scatter entirely (their "change since peak" is 0% by
  construction, so they'd all stack on one point) and broken out in a bar
  chart instead.
- **Binary progress/business-as-usual series** (`decoupled` and `direction` in
  `findings.md`) reuses the same two hues throughout: blue for the "improving"
  side, orange for the opposite. Deliberately not red/green, which fails the
  colorblind check outright (ΔE ~4, well under the ~6 floor) despite looking
  fine to most readers.
- **"Two ways of measuring the same quantity" always gets aqua/gold**
  (`#1baf7a`/`#eda100` light, `#199e70`/`#c98500` dark). Three charts in
  `findings.md` use it: CO₂ share vs. population share, territorial vs.
  consumption-based emissions, and share of the cumulative stock vs. share of
  the current flow. They are not good/bad pairs, so borrowing the progress hues
  would import a value judgment the chart isn't making; using one consistent
  pair for the role means a reader who has decoded one of the three has decoded
  all of them.

Adding a new `series=` chart? Pick colors from the same validated set in
`evidence.config.yaml` rather than eyeballing new hex values, and if two
categories carry a value judgment (good/bad, on-track/off-track), avoid a
red/green pairing.

## Deploying to GitHub Pages

The site is fully static. `.github/workflows/pages.yml` materializes the
`publish_site` job (the whole pipeline against the live sources plus this site)
and publishes `reports/build/`. `sources:strict` (which the asset runs) makes a
missing or empty warehouse fail the build rather than deploy an empty dashboard.

The one thing the workflow still does by hand is append `deployment.basePath` to
`evidence.config.yaml` before the build: project Pages serve from a subpath,
Evidence has no env-var equivalent for it, and a committed value would break
`npm run dev` on localhost.
