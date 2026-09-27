---
title: Restatements
description: CO₂ estimates OWID has revised since this warehouse first loaded them.
sidebar_position: 9
sidebar_badge: Method
---

Countries resubmit inventories and OWID recalculates, so the CO₂ figure for a
past year can change. Every model here overwrites the old number; the
`snap_co2_estimates` snapshot keeps it.

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
    title="The 25 largest revisions in tonnes, as a % of the first estimate"
>
    <ReferenceLine y=0 label=" "/>
</ScatterPlot>

Above the line, the current estimate is *higher* than the one this warehouse
first recorded.

<DataTable data={biggest} rows=25>
    <Column id=country_name title="Country"/>
    <Column id=year title="Year" fmt="0"/>
    <Column id=first_co2_mt title="First (Mt)" fmt="0.0"/>
    <Column id=latest_co2_mt title="Now (Mt)" fmt="0.0"/>
    <Column id=co2_mt_change title="Change (Mt)" fmt="0.0" contentType=delta/>
    <Column id=co2_mt_change_pct title="Change" fmt='0.0"%"'/>
    <Column id=version_count title="Versions" fmt="0"/>
</DataTable>

{:else}

## Nothing revised yet

Every country-year is still on version 1. A snapshot can only record a revision
it was present for, so an empty table means nothing has been restated since the
history began, not that nothing is being watched.

{/if}

<Details title="How it is tracked">

The snapshot stores one row per `(country_iso3, year, version)` with the window
each version was valid for, and `marts.fct_co2_estimate_versions` reads the first
and the current version back off it.

It is the one table here that a rebuild cannot reproduce, and this site is rebuilt
from empty on every push. So the history is carried in instead of recomputed: the
[Pages build](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/blob/main/.github/workflows/pages.yml)
copies `history` out of the most recent
[data release](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/releases)
before it builds, and the release does the same from the release before it.
"Watching since" above is therefore the date that chain started, not the date of
this build.

</Details>

---

<small>Source: <a href="https://github.com/owid/co2-data">OWID CO₂</a>, snapshotted
by dbt (<code>history.snap_co2_estimates</code>, SCD2, <code>check</code> strategy on
<code>co2_mt</code> and <code>co2_per_capita</code>, 1990 onwards).
<code>first_loaded_at</code> is when <em>this warehouse</em> first saw the number,
not when OWID first published it.</small>
