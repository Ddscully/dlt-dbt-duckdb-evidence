# For reviewers

The README explains what this pipeline does. This page answers the questions
someone evaluating it as *work* tends to ask, in the order they tend to ask
them. Every number here was measured on this machine or read out of the repo's
own CI history — none of it is estimated.

**The 90-second tour**, if you only open five files:

| File | Why |
|------|-----|
| [`reports/pages/findings.md`](../reports/pages/findings.md) | the analysis, and the "So what" box under each finding |
| [`orchestration/assets.py`](../orchestration/assets.py) | the whole pipeline as one asset graph, including the partitioned WDI load |
| [`dbt/models/marts/fct_emissions_energy.sql`](../dbt/models/marts/fct_emissions_energy.sql) | the join that hangs facts off an explicit country-year spine instead of off whichever source is widest |
| [`ingest/pipeline.py`](../ingest/pipeline.py) | five sources, two write dispositions, and why that has to be two `run()` calls |
| [`CLAUDE.md`](../CLAUDE.md) | every gotcha that cost more than an hour, written down at the point it was learned |

---

## 1. What decision does this serve?

Three real ones, and they are the reason the "So what" boxes exist on the
findings page:

- **Scope 2 disclosure.** `carbon_intensity_elec_g_kwh` *is* the location-based
  grid emission factor — the figure a multi-site company multiplies its metered
  kWh by to produce the electricity line in a CSRD, SECR or CDP filing. Across
  the largest grids it runs 30 g/kWh (Norway) to 717 g/kWh (South Africa), so the
  same 100 GWh site reports ~3 kt CO₂e or ~72 kt depending only on where it sits.
- **Energy cost exposure.** EU household electricity prices at their *published*
  half-year grain, not flattened to an annual average — because the annual
  average hides the thing you'd want to see. The Netherlands went €0.034/kWh in
  2022-S1 to €0.142 in S2; the annual €0.088 is a price nobody paid.
- **Sourcing-country trajectory.** Peak year and distance from peak, per country.
  The large emitters that haven't peaked at all are about half of world
  emissions — "everywhere is decarbonising" is not a safe default about the
  country you actually buy from.

**What it deliberately is not.** There is no entity below the country: no
customer, supplier, site, product or order anywhere in the warehouse. So this
demonstrates modelling at national grain and says nothing about entity
resolution, cohort analysis or transactional dedup. That's the honest boundary,
and closing it is the top of the roadmap — a company-entity grain (SEC XBRL) and
a transactional one, plus the currency and date dimensions that any money-
denominated fact needs.

## 2. What is the freshness SLA, and what happens when it is missed?

Declared in code, not in a wiki — [`orchestration/assets.py`](../orchestration/assets.py):

| Layer | Policy | Meaning |
|-------|--------|---------|
| `raw/*` | `FreshnessPolicy.time_window` | warn at **2 days** without a successful load, fail at **7** |
| staging / marts / analytics | `FreshnessPolicy.cron` | rebuilt by **08:00 UTC** from data no older than the preceding midnight |

These state what *should* be true regardless of whether a run happened, so a
schedule that quietly stopped firing shows as a stale asset rather than as an
absence you have to notice.

**The alerting path is a workflow, not a person watching a dashboard.**
`nightly.yml` runs the full graph against the *live* endpoints daily at 05:20
UTC and opens — or comments on — a `nightly-failure` issue. That is the signal
that an upstream publisher moved, and it's separate from PR CI on purpose: CI
runs against recorded fixtures, so a red PR build means *the repo* broke, never
that OWID was down.

**What blocks.** 113 dbt tests run inside `dbt build`, every one with
`store_failures`, so a red test hands you `select * from
dbt_test__audit.<test_name>` rather than a count. Five Dagster asset checks sit
alongside them, and `site_pages_all_rendered` is blocking and checks page *size*
— `evidence build` exits 0 for a site missing a page, so a route that emitted
only the SvelteKit shell would otherwise deploy green.

**One caveat I'd rather state than have found.** Source freshness reads dlt's
`_dlt_load_id`, which is stamped at ingest. So it measures *our* load, not the
publisher's — a freshness failure means the pipeline stopped running, not that
the data went stale upstream. It is also tautologically green in CI, which loads
and then checks, which is why it's a `just` recipe rather than a workflow step.

## 3. What does a run cost, and how long does it take?

Measured on this machine against the live APIs, per stage:

| Stage | Time | Notes |
|-------|------|-------|
| `just ingest` | **46.7 s** | five sources over the public internet |
| `just dbt-build` | **12.9 s** | 125 nodes — 10 models, 1 snapshot, 1 seed, 113 tests |
| `just transform` | **0.9 s** | Polars derived metric |
| `just pipeline-status` | **0.9 s** | observability tables |
| `just lake` | **2.5 s** | 762 Parquet files, ~32 MB |
| **total** | **≈ 64 s** | of which **73% is waiting on someone else's API** |

Artifacts: a 52 MB DuckDB file, a 32 MB Parquet archive, a 94 MB Evidence site.
Warehouse contents: 79k staging rows, 115k mart rows, 43,138 in the wide fact.

CI, from the repo's own run history (median of successful runs):

| Workflow | Median | What it does |
|----------|--------|--------------|
| `ci` | **92 s** | pytest + the whole asset graph against fixtures, offline |
| `nightly` | **154 s** | the same graph against live sources |
| `pages` | **199 s** | live build + the Evidence site + deploy |
| `release-data` | **178 s** | live build + export + a dated GitHub release |

**The dollar cost is zero**, and I'd rather say that plainly than dress it up:
GitHub Actions' free tier, no cloud warehouse, no credentials, no bill. That is a
property of the scale, not a virtue of the design. The part that transfers is
that the numbers are *measured and tracked* — `analytics.pipeline_*` records load
times, per-layer inventory and per-test failure counts on every run, and
[`reports/pages/pipeline.md`](../reports/pages/pipeline.md) renders them. On a
warehouse that bills by the second, that table is where the invoice comes from.

## 4. What breaks at 1000×?

43k mart rows → 43M. In the order it would actually fail:

1. **The Polars step, first.** `transform/co2_intensity.py` pulls the mart into
   memory as a DataFrame and writes it back. It's a ranked window function, so
   the fix is either the lazy/streaming API or pushing it into dbt SQL where it
   arguably belonged — the layer exists to demonstrate heavy Python transforms
   and this particular transform isn't heavy enough to need one.
2. **The single-writer lock.** Dagster runs `in_process_executor` deliberately,
   because DuckDB takes one writer and a multiprocess executor would just lose
   races for the file lock. Wanting parallel model builds is exactly the point
   where the warehouse stops being one file. That's the migration the shape is
   designed for: dbt, the tests, the asset graph and Evidence all move to
   Snowflake/BigQuery/MotherDuck on a profile change; dlt swaps a destination.
3. **Full-refresh materialisation.** Every mart is `+materialized: table` and
   rebuilt whole; there is **no incremental model in the project**. At 43M rows
   that's the second thing to fix, and the correctness cost is the same tension
   WDI's lookback window already documents — a restated year needs a full
   refresh, so "incremental" and "picks up restatements" are in conflict and you
   have to choose per model.
4. **The Evidence site.** It ships Parquet to the browser and queries it with
   DuckDB-WASM. Lovely at 94 MB, wrong at 94 GB — that becomes a pre-aggregated
   serving layer.

What *doesn't* break, which is the more interesting half: dlt already merges
incrementally on a real primary key with year partitions behind it; the fixtures
keep CI offline and constant-time; and the lake's documented small-file
anti-pattern (275 partitions averaging 47 kB, when ~100 MB is the rule of thumb)
actually *fixes itself* at 1000× — the partition sizes become right and the file
count doesn't move.

## 5. What would I do differently?

The genuine ones, not the diplomatic ones.

- **Model the publisher's grain first, derive the convenient one from it.** I
  modelled Eurostat prices as annual averages because annual joined to everything
  else, and that averaged away the 2021–23 energy crisis. The fix inverted it:
  the half-year model is now the cleaning model and the annual one is a four-line
  average off it. I got the direction wrong once and it's a general rule.
- **A lint that passes because it's linting nothing is worse than no lint.**
  sqlfluff was silently broken from the first commit — configured, running,
  green, checking nothing. Nothing failed; that was the problem.
- **Design against silently-wrong, not against loud failure.** The worst bug here
  never errored: guarding a partitioned asset on `has_partition_key_range` alone
  meant `--partition 1995` fell through to the incremental branch and
  *succeeded*, reporting 1995 in the UI while loading the last five years. Same
  shape as DuckDB's `COPY … (overwrite true)` leaving a stale partition file that
  keeps answering queries, and as an Evidence column named `tests` drawing a
  chart with no bars and no error anywhere. Loud failures are cheap.
- **Decide where the project root comes from on day one.** `REPO_ROOT` started in
  `ingest/pipeline.py` and got imported from there by the lake, the exporter and
  the report builder — so every layer's sense of where it was depended on where
  *ingestion* sat. It's now `modern_data_stack.paths` with an explicit
  three-step resolution that raises rather than falling back to the cwd, because
  the cwd fallback resolves to a path DuckDB then *creates*: a run that goes
  green against an empty database.
- **The lake is an archive, not a landing zone**, which inverts how that layer is
  usually drawn. I'd do it again — dlt's filesystem destination can't partition
  by a data column and reversing the flow would have cost schema inference and
  the raw freshness checks — but it's a compromise and the docs say so rather
  than implying the tidy version.
- **The gaps I'd close next, in order:** a date dimension and currency handling
  (there is neither, and every money question downstream is wrong without them),
  one incremental model, then an entity grain below the country.

---

<sub>Ideas and their post-mortems accumulate in `CLAUDE.md`; it is the file to
read if you want to know what this cost to learn rather than what it does.</sub>
