# 2026-07-17 — Fix the "thousands of percent" renewables axis

Short debugging session on the Evidence dashboard. The "Renewables adoption vs.
life expectancy" bubble chart was showing an x-axis in the **thousands of
percent** (e.g. ~8,000% instead of ~80%).

## Symptom

On `reports/pages/index.md`, the renewables-vs-life-expectancy bubble chart's
x-axis and the "Avg renewables share" KPI both rendered renewables share as a
number ~100× too large.

## Root cause (confirmed in Evidence's source, not guessed)

The data is correct — `marts.fct_emissions_energy.renewables_share_pct` genuinely
stores a percentage (Iceland 2019 = `80.843`, avg ≈ `13.6`). The inflation is
purely Evidence's **automatic column formatting**:

- Evidence extracts a format tag from the substring after the last `_` in a
  column name (`@evidence-dev/component-utilities/src/formatting.js`,
  `maybeExtractFormatTag`). For `renewables_share_pct` that tag is **`pct`**.
- The built-in `pct` format's auto-formatter runs
  `ssf.format(code, typedValue * 100) + '%'`
  (`.../src/builtInFormats.js`) — it assumes the value is a **fraction** (0–1)
  and multiplies by 100.
- Value already a percentage → `80.843` renders as `8,084%`.

Two spots were affected:

1. **Bubble chart x-axis** — `x=renewables_share_pct`, auto-formatted via the
   `_pct` suffix.
2. **"Avg renewables share" KPI** — `fmt="0.0\%"`, where ssf's `%` code *also*
   multiplies by 100 (~13.6 → `1,360%`).

The most-efficient-economies DataTable was already fine — its explicit
`fmt="0.0"` overrides the auto-format.

## Fix (report-only, no pipeline/mart changes)

In `reports/pages/index.md`:

- Bubble-chart query: aliased `renewables_share_pct as renewables_share` and set
  `x=renewables_share`. Dropping the `_pct` suffix stops the ×100 auto-format;
  axis now plots 0–80. The axis title already reads "(%)".
- KPI tile: `fmt="0.0\%"` → `fmt='0.0"%"'`. The quoted `"%"` is literal text, so
  it appends a percent sign **without** re-scaling → `13.6%`.

## Verifying

- Traced the behaviour directly in `reports/node_modules/@evidence-dev/
  component-utilities/src/` (formatting.js + builtInFormats.js) rather than
  assuming.
- Ran `npm run dev` (Evidence dev server, Vite on `localhost:3000`); page
  returned HTTP 200 with no query/build errors. Canvas axis to be eyeballed in a
  browser (x-axis ≤ ~80, KPI ≈ 13.6%).

## Side fix

- Cleared the recurring `Browserslist: browsers data (caniuse-lite) is 6 months
  old` warning with `npx update-browserslist-db@latest` in `reports/`
  (`caniuse-lite` 1.0.30001769 → 1.0.30001806). Harmless warning; only affects
  browser-target accuracy.

## Gotcha for next time

**Evidence auto-formats numeric columns by name suffix.** A `_pct` (or `_usd`,
etc.) suffix triggers a built-in format; `_pct` assumes a 0–1 fraction and
multiplies by 100. If a column already holds a percentage, either store it as a
fraction, drop the suffix, or set an explicit `fmt`/`xFmt` to override.
