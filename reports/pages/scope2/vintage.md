---
title: Vintage and restatements
description: Which year each country's latest Scope 2 factor belongs to, which countries lag, and which factors have been revised since first published.
sidebar_position: 2
---

[← Scope 2 Factors](/scope2)

**"The most recent published factor" resolves to a different year for different
countries, and a published year can be revised afterwards. A filing has to be
able to say which version it used.**

```sql vintage
with by_year as (
    select latest_available_year, count(*) as n_countries
    from warehouse.grid_emission_factors
    where is_latest_available
    group by latest_available_year
)

select
    cast(cast(latest_available_year as integer) as varchar) as vintage_year,
    n_countries                                             as countries,
    100.0 * sum(n_countries) over (order by latest_available_year desc)
        / sum(n_countries) over ()                          as coverage
from by_year
order by latest_available_year
```

<BarChart
    data={vintage}
    x=vintage_year
    y=countries
    y2=coverage
    y2SeriesType=line
    y2Fmt='0"%"'
    y2Min={0}
    sort=false
    color="#2a78d6"
    xAxisTitle="Vintage"
    yAxisTitle="Countries"
    y2AxisTitle="Covered by this cut-off"
/>

The bars are the distribution; the line is what a reporter has to decide.
Insisting on a 2025 factor covers **43%** of countries. Accepting 2024 or later
covers **94%**, and the two years below that add the last six points. Filtering
the table to a single latest year would silently drop more than half the world,
and tightening the cut-off by one year costs fifty points of coverage.

## The countries furthest behind

```sql stale
select
    country_name,
    region,
    latest_available_year,
    latest_factor_lag_years,
    emission_factor_g_co2_per_kwh,
    electricity_generation_twh
from warehouse.grid_emission_factors
where is_latest_available
  and latest_factor_lag_years >= 2
order by electricity_generation_twh desc
```

<DataTable data={stale} rows=12>
    <Column id=country_name title="Country"/>
    <Column id=latest_available_year title="Newest factor" fmt="0"/>
    <Column id=latest_factor_lag_years title="Years behind" fmt="0"/>
    <Column id=emission_factor_g_co2_per_kwh title="gCO₂e / kWh" fmt="#,##0.0"/>
    <Column id=electricity_generation_twh title="Grid (TWh)" fmt="#,##0.0"/>
</DataTable>

Grid size is no protection: Ukraine's most recent published factor is 2022, on a
111 TWh grid.

## Has the factor been restated?

A disclosure is filed against the factor published *at the time*. When the
publisher revises that year afterwards, the filing does not become wrong; it
becomes a filing against a superseded factor, which you have to be able to
demonstrate. This warehouse keeps every version of every factor from 2015 on.

```sql restatements
select
    country_name,
    year,
    first_published_factor_g_co2_per_kwh as first_factor,
    emission_factor_g_co2_per_kwh        as current_factor,
    emission_factor_g_co2_per_kwh
        - first_published_factor_g_co2_per_kwh as change_g,
    factor_version_count,
    last_revised_at
from warehouse.grid_emission_factors
where is_restated
order by abs(
    emission_factor_g_co2_per_kwh - first_published_factor_g_co2_per_kwh
) desc
limit 20
```

{#if restatements.length > 0}

<DataTable data={restatements} rows=20>
    <Column id=country_name title="Country"/>
    <Column id=year title="Factor year" fmt="0"/>
    <Column id=first_factor title="As first published" fmt="#,##0.0"/>
    <Column id=current_factor title="Now" fmt="#,##0.0"/>
    <Column id=change_g title="Change (g/kWh)" fmt="#,##0.0" contentType=delta/>
    <Column id=factor_version_count title="Versions" fmt="0"/>
</DataTable>

Each of these is a country-year whose factor moved after this warehouse first
recorded it. A Scope 2 line filed on the earlier number is reconcilable to the
later one only because both are still here.

{:else}

**Nothing restated yet**, and on a warehouse built from scratch that is the honest
answer rather than a broken query: a snapshot can only record a revision it was
present for. The first run stores version 1 of every factor, and a row becomes
restated the first time a later run finds a different number.

That version history is the one part of this table a rebuild cannot reproduce, so
every build carries it forward from the previous
[data release](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/releases)
instead of recomputing it. The [Restatements page](/restatements) does the same
for OWID's CO₂ estimates.

{/if}
