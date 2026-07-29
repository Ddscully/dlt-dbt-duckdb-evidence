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
  selector at the top of `pages/index.md`.
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

Empty output means clean. Then confirm the numbers look sane — a formatting bug
shows up as absurd percentages:

```bash
grep -rho '"[0-9]\{3,\}%"\|[0-9]\{1,\},[0-9]\{3\}%' reports/build/
```

## Node, not uv

This is the one layer that isn't Python. It needs Node; `npm install` runs from
`reports/`. `reports/node_modules/` and `reports/build/` are gitignored, as is
`reports/.evidence/`.
