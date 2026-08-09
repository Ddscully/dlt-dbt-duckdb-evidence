---
name: building-evidence-reports
description: Working on the Evidence dashboard in reports/ — source queries, page components, the stale schema cache, and building the static site. Use when editing reports/pages/*.md, reports/sources/**, or when an Evidence build fails with a column or schema validation error.
---

# Building Evidence reports

`reports/` is an [Evidence](https://evidence.dev) project: SQL + markdown compile
to a static site. It reads the same DuckDB file as everything else, so the
warehouse must exist first (`just run`).

There is no vendor agent skill for Evidence — this is the repo's own guidance.
Component reference lives at <https://docs.evidence.dev>.

## The one gotcha that will cost you an hour

**Evidence caches each source's schema keyed on the source SQL.** A source query
like `select * from marts.fct_emissions_energy` that gains a column produces
*identical SQL text*, so the cache looks fresh — and the build then validates
your page against the stale schema and fails with a missing-column error.

After any change to mart or analytics columns:

```bash
just report-clean   # rm -rf .evidence build && npm install && npm run sources && npm run build
```

not `just report`. `just report` is only safe when the warehouse schema is
unchanged.

## A source query must return rows

A source that comes back empty is written as a 0-byte parquet and the *build*
fails on it: `Invalid Input Error: File 'warehouse_x.parquet' too small to be a
Parquet file`. So don't put the interesting filter in the source — select the
whole table there and filter in the page's own SQL block, where an empty result
is fine (components render their empty state). `co2_estimate_versions.sql` is
the example: unfiltered source, `where is_revised` on the page.

Pair that with an explicit empty branch when the table can legitimately be
empty:

````markdown
{#if biggest.length > 0}
<DataTable data={biggest}/>
{:else}
Nothing recorded yet — and here's why that's expected.
{/if}
````

## How it's wired

| Path | Role |
|---|---|
| `sources/warehouse/connection.yaml` | DuckDB source → `../data/warehouse.duckdb` |
| `sources/warehouse/*.sql` | One file per source query; the filename is the reference name |
| `pages/*.md` | One page each; frontmatter title + SQL blocks + components |

A file at `sources/warehouse/emissions_energy.sql` is referenced in pages as
`warehouse.emissions_energy`. Adding a new source query means adding a `.sql`
file there, then `npm run sources`.

## Writing a page

SQL blocks are named and become queryable results:

````markdown
```sql renew_vs_life
select country_iso3, renewables_share_pct, life_expectancy
from warehouse.emissions_energy
where year = ${inputs.year.value}
```

<ScatterPlot data={renew_vs_life} x=renewables_share_pct y=life_expectancy/>
````

- Inputs interpolate as `${inputs.<name>.value}` — see the `<Dropdown>` year
  selector at the top of `pages/index.md`. Use `.value` inside SQL (it's a SQL
  fragment) and `{inputs.<name>.label}` in prose; `.value` in prose renders the
  raw fragment. `<Value data={q} column=year/>` is *not* a substitute — it shows
  the first row of that query, not the selection, so it silently disagrees with
  the charts as soon as the user picks another year.
- **`<Alert>` needs blank lines around its content.** It's the callout component
  (`findings.md` uses it for the "So what" boxes) and takes
  `status="base|info|positive|warning|negative"` — the older `default`/`danger`/
  `success` spellings still render but log a deprecation warning. Written tight
  against the tags, the markdown inside comes out literal (`**text**`):

  ````markdown
  <Alert status=info>

  **So what.** Body text, which may span paragraphs.

  </Alert>
  ````

  It isn't in the components list most Evidence docs pages show; the exhaustive
  answer is `ls reports/node_modules/@evidence-dev/core-components/dist/{atoms,molecules,organisms,unsorted/ui}`.
- **`<Dropdown defaultValue>` must be braced if the values are numbers.**
  `defaultValue=2024` is the *string* `"2024"`; DuckDB hands the options back as
  doubles (`2024.0`), the strict-equality match fails, and no option is selected.
  Nothing errors: `inputs.year.value` stays at Evidence's
  `(SELECT NULL WHERE 0 /* An Input has not been set */)` sentinel, so every
  query filtering on it sits at `Loading...` forever while input-free charts on
  the same page render normally. Write `defaultValue={2024}`.
- **Same cause, second symptom: a year cast to a string on a page renders
  `2025.0`.** The extractor writes every numeric column to parquet as `DOUBLE`,
  and page SQL runs in the browser against that — so `cast(year as varchar)`
  stringifies a double. It's a string by then, so no `fmt` fixes it. Where the
  value stays numeric (DataTable column, `<BigValue>`) leave it alone and pass
  `fmt="0"`; where it has to be text (a chart's category axis) write
  `cast(cast(year as integer) as varchar)`. A *source* query is server-side and
  needs neither, which is why `latest_years.sql`'s `*_label` columns look
  simpler than they can be on a page.
- **`<Value>` emits a trailing space, so never put punctuation straight after
  one.** `<Value .../>.` renders as "Turkmenistan ." and `<Value .../>'s` as
  "Norway 's". End the clause on words instead: `... at <Value .../> g/kWh.`
- **Keep a sentence containing a `<Value>` on one source line.** A wrapped
  paragraph that has a component in it stops processing markdown at the wrap, so
  a `[link](/foo)` later in the same paragraph renders as literal brackets. Long
  lines are the price; a following paragraph with no component is unaffected.
- Don't hardcode the upper bound of a year selector (`where year between 1990
  and 2022`) — it silently pins the dashboard to whatever year the warehouse
  held the day it was written. `where year >= 1990` tracks the data.
- Charts and `<BigValue>` need explicit `fmt=` for anything that isn't a plain
  number; percentages in this warehouse are stored 0–100, so `fmt='0.0"%"'`, not
  a percent format that multiplies by 100.
- Filter nulls in the SQL. `electricity_price_eur_kwh` is null outside the
  EU/EEA and `life_expectancy` is sparse in early years — unfiltered they render
  as gaps or drag averages.

## Verify a build actually succeeded

Evidence exits 0 on some failures and writes the error into the page, so check
the output rather than the exit code:

```bash
just report-clean
grep -oiE '(could not|error|does not exist|no such|binder error|catalog error)[^<]{0,60}' reports/build/index.html
```

Empty output means clean — but **`error: null` is a false positive on every
page**, including ones you didn't touch: it's a field in SvelteKit's embedded
data payload. Diff against a page you didn't change before believing any hit. Then confirm the numbers look sane — a formatting bug
shows up as absurd percentages:

```bash
grep -rho '"[0-9]\{3,\}%"\|[0-9]\{1,\},[0-9]\{3\}%' reports/build/
```

### Text dumps can't tell working from broken

**`Loading...` in the built HTML is normal.** Evidence prerenders the page shell
and fetches results client-side (DuckDB-WASM reading the parquet in
`build/data/`), so `curl`, `grep` and any headless dump of the *text* show
`Loading...` for every component whether or not the page works. Only a browser
that runs JS can tell you.

To actually look at the page:

```bash
cd reports && npm run preview            # serves build/ on :3000 with range support
chromium --headless=old --no-sandbox --disable-gpu --window-size=1400,2100 \
  --virtual-time-budget=60000 --screenshot=/tmp/page.png http://localhost:3000/
```

- Use `--headless=old`. On this machine snap chromium's *new* headless mode
  silently produces nothing — no DOM, no screenshot, no stderr, exit 0. Same for
  `--dump-dom` and `--enable-logging=stderr` in either mode.
- `python3 -m http.server` is not good enough for `build/`; it has no Range
  support, which DuckDB-WASM needs for the parquet.
- **The 60 s budget is load-bearing on a long page, and cutting it fakes a bug.**
  At 25 s, a page with three charts screenshots with one of them showing its axis,
  its categories and its value labels and *no bars* — a different chart on each
  run, which reads exactly like the reserved-column-name failure and sends you
  editing SQL that was fine. Shoot twice before believing a chart is broken: a
  real failure hits the same chart every time.
- For console errors and failed requests, drive it over CDP instead: launch with
  `--remote-debugging-port=9222`, then connect from a Node script (Node 22 has a
  global `WebSocket`) and subscribe to `Runtime.consoleAPICalled`,
  `Runtime.exceptionThrown` and `Network.loadingFailed`.
- `build/api/prerendered_queries/*.arrow` holds the build-time results — reading
  their row counts with `pyarrow` tells you which queries produced data and which
  came back empty, without a browser at all.

## Node, not uv

This is the one layer that isn't Python. It needs Node; `npm install` runs from
`reports/`. `reports/node_modules/` and `reports/build/` are gitignored, as is
`reports/.evidence/`.
