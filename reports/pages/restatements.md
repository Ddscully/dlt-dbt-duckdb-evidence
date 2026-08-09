---
title: Restatements
description: CO₂ estimates OWID has revised since this warehouse first loaded them.
sidebar_position: 8
sidebar_badge: Method
---

Emissions data is not a fixed record. Countries resubmit inventories, OWID
recalculates, and the figure published for 2019 is not necessarily the figure you
will read for 2019 next year. Every model in this project overwrites the old
number on each run, so a revision would normally leave no trace at all. The
`snap_co2_estimates` dbt snapshot is what keeps them.

```sql summary
select
    count(*)                                     as country_years,
    count(*) filter (where is_revised)           as revised,
    count(distinct case when is_revised then country_iso3 end) as countries_revised,
    min(first_loaded_at)                         as watching_since
from warehouse.co2_estimate_versions
```

<Grid cols=3>
    <BigValue data={summary} value=country_years title="Country-years tracked" fmt=num0/>
    <BigValue data={summary} value=revised title="Revised since first load" fmt=num0/>
    <BigValue data={summary} value=watching_since title="Watching since" fmt="yyyy-mm-dd"/>
</Grid>

```sql biggest
select
    country_name,
    region,
    year,
    first_co2_mt,
    latest_co2_mt,
    co2_mt_change,
    co2_mt_change_pct,
    version_count,
    last_revised_at
from warehouse.co2_estimate_versions
where is_revised
order by abs(co2_mt_change) desc
limit 25
```

{#if biggest.length > 0}

## Largest revisions

<ScatterPlot
    data={biggest}
    x=year
    y=co2_mt_change_pct
    series=region
    xFmt="0"
    yFmt='0.0"%"'
    xAxisTitle="Year restated"
    yAxisTitle="Change vs. first estimate"
    tooltipTitle=country_name
/>

The 25 largest changes in absolute tonnes. A positive change means the current
estimate is *higher* than the one this warehouse first recorded.

<DataTable data={biggest} rows=25>
    <Column id=country_name title="Country"/>
    <Column id=year title="Year" fmt="0"/>
    <Column id=first_co2_mt title="First (Mt)" fmt="0.0"/>
    <Column id=latest_co2_mt title="Now (Mt)" fmt="0.0"/>
    <Column id=co2_mt_change title="Change (Mt)" fmt="0.0"/>
    <Column id=co2_mt_change_pct title="Change" fmt='0.0"%"'/>
    <Column id=version_count title="Versions" fmt="0"/>
</DataTable>

## How it is tracked

The snapshot stores one row per `(country_iso3, year, version)` with the window
each version was valid for, and `marts.fct_co2_estimate_versions` reads the first
and the current version back off it.

It is also the one table here that a rebuild cannot reproduce, and this site is
rebuilt from empty on every push. So the history is carried in rather than
recomputed: the
[Pages build](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/blob/main/.github/workflows/pages.yml)
copies `history` out of the most recent
[data release](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/releases)
before it builds, and the release does the same from the release before it.
"Watching since" above is therefore the date that chain started, not the date of
this build.

{:else}

## Nothing revised yet

Every country-year above is on version 1, and on a warehouse that was just built
that is what it *should* say: a snapshot can only record a revision it was
present for. The first run stores version 1 of everything, and a row becomes
revised the first time a later run finds a different number. The snapshot stores
one row per `(country_iso3, year, version)` with the window each version was
valid for, and `marts.fct_co2_estimate_versions` reads the first and the current
version back off it.

That makes the snapshot the one table here that isn't reproducible from the
sources. Rebuild the warehouse from scratch and the history is gone, which is
why it is carried in rather than recomputed: the
[Pages build](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/blob/main/.github/workflows/pages.yml)
copies `history` out of the most recent
[data release](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/releases)
before it builds, and the release does the same from the release before it. So an
empty table here means nothing has been restated since that chain began, not
that nothing is being watched. Before the first data release was cut there was
nothing to carry, and the page was empty for that reason instead.

{/if}

---

<small>Source: <a href="https://github.com/owid/co2-data">OWID CO₂</a>, snapshotted
by dbt (<code>history.snap_co2_estimates</code>, SCD2, <code>check</code> strategy on
<code>co2_mt</code> and <code>co2_per_capita</code>, 1990 onwards).
<code>first_loaded_at</code> is when <em>this warehouse</em> first saw the number,
not when OWID first published it.</small>
