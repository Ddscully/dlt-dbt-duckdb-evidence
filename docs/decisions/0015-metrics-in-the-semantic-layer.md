# 0015. Metrics are defined in dbt's semantic layer and compiled by MetricFlow's engine, run on our own connection

Status: accepted 2026-09-28

## Context

The finance agent (`docs/FINANCE_AGENT.md`) needs totals and breakdowns by
name — net revenue by quarter, orders by region — without a model writing SQL.
Each metric's definition has to live somewhere, with a filter (revenue lines
only, sales only), an aggregation, and the dimensions it may be grouped by.

Measured before building anything (warehouse built 2026-09-28):

- **MetricFlow is already installed.** dbt-core 1.12 requires `metricflow`, so
  the semantic layer costs no new package, only a declaration.
- **Its Python engine compiles without a connection.** Given the parsed
  `semantic_manifest.json` and a client that only renders SQL, it compiles a
  request in about a hundredth of a second, and the SQL runs on the agent's own
  read-only DuckDB connection. The figures matched direct SQL: net revenue
  £9,570,429.41 for 2010, and 20,625 orders.
- **It trusts the yml completely.** It does not read the `additivity` labels:
  a measure summing `unit_price` compiled and returned £2.5m for 2010. It does
  not roll distinct counts up: customers by year summed to far more than the
  customers of all years. A group with no returns returned no return rate
  rather than 0% until each simple metric had `fill_nulls_with: 0`.

## Decision

- **Metrics are dbt semantic models and metrics**, in
  `dbt/models/marts/retail/_retail_metrics.yml`, beside the models they read,
  and `dim_date` is the time spine.
- **`agent/metrics.py` compiles them with MetricFlow's engine and runs the SQL
  itself**, read-only, through a client that refuses to execute anything.
- **The yml is held to the column labels by a test**
  (`tests/test_semantic_layer.py`): measures are bare columns, a sum reads only
  an `additive` column, and no entity or dimension reads a `direct_identifier`
  column.
- **Retail only**, for now.
- **Years the data covers in part are reported as they are**, and the tool's
  note names each one for the loop to append verbatim, pointing at
  `explain_change` for comparisons.

## Rejected

- **Letting the model write SQL.** Nothing could hold arbitrary SQL to the
  additivity labels or keep `customer_id` out of a `group by`, and a small model
  measured here summed the figures it was handed, wrongly.
- **A Python registry of metric SQL in `agent/`.** It works, and it is a second
  place a metric's meaning lives, beside the ymls that already carry the
  columns' descriptions and labels; adding a metric would be a code change.
- **MetricFlow's `mf` CLI (`dbt-metricflow`).** It reads
  `target/semantic_manifest.json` whatever `DBT_TARGET_PATH` says, so a fixture
  run and the real one would read the same file, and it opens the warehouse
  through a dbt adapter rather than the read-only connection the agent holds.
  On a machine with TeX installed, `mf` is Metafont.
- **dbt Cloud's Semantic Layer APIs.** A hosted service, for a repo that runs
  on a laptop against one file.
- **Aligning partly covered years automatically.** It would change what "2010
  revenue" means in a plain total. Aligning is right for a comparison, which is
  why `explain_change` does it
  ([0013](0013-compare-aligned-periods.md)).

## Consequences

MetricFlow's engine is not a documented public API and moves with dbt-core, so
a dbt upgrade can break `agent/metrics.py`; the test that compiles every metric
is where that shows. Four things stay this repo's own, because MetricFlow does
not do them: the additivity test, the `all` row from a second ungrouped query,
rounding money to the penny (float sums differ between runs in the ninth
significant figure), and naming the in-memory test database `warehouse`,
because the compiled SQL names its tables after the real file. The
`country_stats` metrics are a later step, where `gdp_constant_usd` and the
income group as it stood each year can become rules the semantic layer
enforces.
