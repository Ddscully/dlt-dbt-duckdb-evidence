---
title: Pipeline
description: The state of the pipeline that built this site, from load times to layer sizes and every dbt test.
sidebar_position: 9
sidebar_badge: Ops
---

Operational state of the pipeline behind this site: when each source last loaded,
how many rows survived each layer, and the current result of every data-quality
test.

```sql test_summary
select
    count(*)                              as total_tests,
    count(*) filter (where status = 'fail') as failing_tests,
    sum(failing_rows)                     as failing_rows,
    count(distinct tested_model)          as models_covered
from warehouse.pipeline_tests
```

```sql layer_summary
select
    (select count(*) from warehouse.pipeline_sources) as sources,
    (select sum(rows) from warehouse.pipeline_sources) as source_rows,
    (select sum(rows) from warehouse.pipeline_tables) as modelled_rows
```

<Grid cols=4>
    <BigValue data={test_summary} value=total_tests title="dbt tests"/>
    <BigValue data={test_summary} value=failing_tests title="Failing"/>
    <BigValue data={layer_summary} value=source_rows fmt="#,##0" title="Rows landed"/>
    <BigValue data={layer_summary} value=modelled_rows fmt="#,##0" title="Rows modelled"/>
</Grid>

## Source freshness

```sql sources
select source_table, rows, year_min, year_max, loaded_at
from warehouse.pipeline_sources
order by rows desc
```

<DataTable data={sources} rows=5>
    <Column id=source_table title="Landing table"/>
    <Column id=rows title="Rows" fmt="#,##0"/>
    <Column id=year_min title="From" fmt="0"/>
    <Column id=year_max title="To" fmt="0"/>
    <Column id=loaded_at title="Loaded" fmt="yyyy-mm-dd hh:mm"/>
</DataTable>

`loaded_at` comes from dlt's `_dlt_load_id`, a unix epoch stamped at ingest. It
measures **our load, not the publisher's release**: a stale timestamp here means
the pipeline stopped running, not that OWID stopped publishing. That is also why
it is tautologically green on a freshly built copy of this site, since the build
loads the data and then reports on the load.

Note the two distinct timestamps. Four resources load with `replace` and `wb_wdi`
loads with `merge`, and `refresh` is an argument to a dlt *run* rather than a
property of a resource, so a single run cannot refresh the first group while
leaving the incremental one alone. It is two loads, seconds apart, and this table
is where that shows up.

## What each layer holds

```sql tables
select layer, table_name, rows, year_min, year_max
from warehouse.pipeline_tables
order by layer, table_name
```

<DataTable data={tables} rows=10>
    <Column id=layer title="Layer"/>
    <Column id=table_name title="Table"/>
    <Column id=rows title="Rows" fmt="#,##0"/>
    <Column id=year_min title="From" fmt="0"/>
    <Column id=year_max title="To" fmt="0"/>
</DataTable>

`marts.dim_country_year` is larger than the fact it feeds, and that is the
design: the spine is the complete cross join, the fact is the part of it any
source reports. The difference is the subject of the [coverage page](/coverage).

`history.snap_co2_estimates` is the one table here a rebuild cannot reproduce.
Every other row above is derivable from the sources; the snapshot is accumulated
state, and deleting the warehouse destroys it for good.

## Test coverage

```sql tests_by_model
-- `n_tests`, not `tests`. A column called `tests` collides with something inside
-- Evidence's chart components: the BarChart renders its axis, categories and
-- value labels perfectly and simply draws no bars, with no error anywhere. The
-- only fix is the rename. `rows` behaves the same way: fine in a DataTable,
-- silently barless in a chart.
select
    tested_model,
    count(*)          as n_tests,
    sum(failing_rows) as n_failing_rows
from warehouse.pipeline_tests
where tested_model is not null
group by tested_model
order by n_tests desc
```

<BarChart
    data={tests_by_model}
    x=tested_model
    y=n_tests
    swapXY=true
    sort=false
    color="#2a78d6"
    labels=true
    labelFmt="0"
    xAxisTitle="Model"
    yAxisTitle="Tests"
/>

```sql tests_by_type
select test_type, count(*) as tests
from warehouse.pipeline_tests
group by test_type
order by tests desc
```

<DataTable data={tests_by_type} rows=6>
    <Column id=test_type title="Test type"/>
    <Column id=tests title="Count" fmt="0"/>
</DataTable>

The distribution is deliberate. `accepted_range` dominates because the failure
mode this warehouse actually has is a plausible-looking wrong number rather than
a missing one: a unit error, a percentage over 100, a year outside a source's
range. The `unique_combination_of_columns` tests are the grain contract,
`(country_iso3, year)` on every fact-shaped model, which is what catches a
duplicate on either side of a join fanning rows out downstream.

`dbt_project.yml` also sets `+store_failures: true` project-wide, so a test does
not just return a count. It leaves the offending rows behind in
`dbt_test__audit.<test_name>`, and a red check gives you the rows rather than a
number.

The bounds are calibrated to fail on bugs rather than on reality, which sometimes
means *not* testing a column. `trade_co2_share` has no range test because its
real range runs from about −98% to +1023%, and `income_group` is nullable on
purpose because the World Bank does not classify every territory.

## Currently failing

```sql failing
select test_name, test_type, tested_model, tested_column, failing_rows, audit_table
from warehouse.pipeline_tests
where status = 'fail'
order by failing_rows desc
```

{#if failing.length > 0}

<DataTable data={failing} rows=20>
    <Column id=test_name title="Test"/>
    <Column id=tested_model title="Model"/>
    <Column id=tested_column title="Column"/>
    <Column id=failing_rows title="Rows" fmt="#,##0"/>
</DataTable>

Each row above has a table behind it. `select * from` the `audit_table` value to
see the exact rows that failed, rather than reproducing the test by hand.

{:else}

Nothing is failing, which on a green build is the expected state. This section is
not broken: every one of the tests above holds, so every `dbt_test__audit` table
is empty. If one were failing, this table would name it and point at the audit
table holding the offending rows.

{/if}

## Where these numbers come from

None of this is instrumentation added for the purpose. dlt stamps `_dlt_load_id`
on every landing row, dbt stores each failing test row in a `dbt_test__audit`
table, and `information_schema` knows the shape of every layer.
`transform/pipeline_status.py` walks all three into `analytics.pipeline_sources`,
`pipeline_tables` and `pipeline_tests`, because two of them need dynamic SQL over
a variable table list and one needs a file that lives outside the database.

---

<small>Written by <code>transform/pipeline_status.py</code> (<code>just
pipeline-status</code>, part of <code>just run</code> and the Dagster asset
<code>analytics/pipeline_status</code>). A snapshot taken at build time rather
than a history: nothing here accumulates across runs.</small>
