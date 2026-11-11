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
-- One row per vintage, with the share of countries a cut-off at that year
-- keeps: sources/warehouse/grid_factor_vintage.sql.
select vintage_year, countries, coverage
from warehouse.grid_factor_vintage
order by factor_year
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
    title="Countries by the year of their latest factor"
/>

```sql vintage_facts
-- The two cut-offs the paragraph below quotes, read from the same rows as the
-- chart so neither names a year by hand. `coverage` is cumulative from the
-- newest year down, so the largest value at or above a year is that cut-off's.
with vintages as (
    select *, max(factor_year) over () as newest_year
    from warehouse.grid_factor_vintage
)

select
    cast(cast(max(newest_year) as integer) as varchar)     as newest_label,
    cast(cast(max(newest_year) - 1 as integer) as varchar) as previous_label,
    max(coverage) filter (where factor_year = newest_year)      as newest_coverage,
    max(coverage) filter (where factor_year >= newest_year - 1) as previous_coverage
from vintages
```

The bars are the distribution; the line is what a reporter has to decide.

Insisting on a <Value data={vintage_facts} column=newest_label/> factor covers <Value data={vintage_facts} column=newest_coverage fmt='0"%"'/> of countries, and accepting <Value data={vintage_facts} column=previous_label/> or later covers <Value data={vintage_facts} column=previous_coverage fmt='0"%"'/> of them.

Filtering the table to a single latest year silently drops every country behind
it, and the line shows what each further year of tolerance buys back.

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

---

<small>Source: <a href="https://github.com/owid/energy-data">OWID Energy</a>
(<code>carbon_intensity_elec</code>, from Ember's lifecycle estimates), modelled as
<code>marts.dim_grid_emission_factors</code>. Nothing here is advice on how to file.</small>
