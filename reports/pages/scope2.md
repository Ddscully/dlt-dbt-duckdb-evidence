---
title: Scope 2 Factors
description: Grid emission factors packaged as a reference table, with the vintage, the lineage and a worked example of the disclosure line they feed.
---

Every other page here reads `carbon_intensity_elec_g_kwh` as a climate statistic.
This one reads it as what it also is: the **location-based Scope 2 emission
factor** under the GHG Protocol, the number a company multiplies its metered kWh
by to produce the purchased-electricity line of a CSRD, SECR or CDP disclosure.

Nothing new is computed. `marts.dim_grid_emission_factors` is the same series,
packaged for that use: the factor in the unit meter data arrives in, the year it
belongs to, how stale that year is, which extract it came out of, and whether it
has been restated since this warehouse first recorded it.

```sql headline
-- The double cast is not redundant. Evidence's DuckDB extractor writes every
-- numeric column to parquet as DOUBLE, so a page-level `cast(year as varchar)`
-- runs over a double and produces '2025.0'. Casting back to integer first is what
-- makes a year render as a year. See reports/README.md.
select
    count(*)                                                as n_countries,
    cast(cast(max(latest_available_year) as integer) as varchar) as newest_vintage,
    count(*) filter (where latest_factor_lag_years = 0)      as n_at_frontier,
    count(*) filter (where latest_factor_lag_years >= 2)     as n_stale
from warehouse.grid_emission_factors
where is_latest_available
```

```sql spread
-- Grids above 10 TWh only. Below that a single new plant swings the factor, and
-- the extremes become one diesel island against one hydro island.
select
    min(emission_factor_g_co2_per_kwh)                     as cleanest,
    max(emission_factor_g_co2_per_kwh)                     as dirtiest,
    max(emission_factor_g_co2_per_kwh)
        / min(emission_factor_g_co2_per_kwh)               as ratio,
    arg_min(country_name, emission_factor_g_co2_per_kwh)   as cleanest_country,
    arg_max(country_name, emission_factor_g_co2_per_kwh)   as dirtiest_country,
    count(*)                                               as n_countries
from warehouse.grid_emission_factors
where is_latest_available
  and electricity_generation_twh > 10
```

<Grid cols=4>
    <BigValue data={headline} value=n_countries title="Countries with a factor"/>
    <BigValue data={headline} value=newest_vintage title="Newest factor year"/>
    <BigValue data={headline} value=n_at_frontier title="Countries at that year"/>
    <BigValue data={spread} value=ratio fmt='0"×"' title="Spread across grids >10 TWh"/>
</Grid>

Across the <Value data={spread} column=n_countries/> countries with a grid above 10 TWh, the current factor runs from <Value data={spread} column=cleanest_country/> at <Value data={spread} column=cleanest fmt="0.0"/> up to <Value data={spread} column=dirtiest_country/> at <Value data={spread} column=dirtiest fmt="#,##0"/> grams of CO₂ per kWh.

That is a wider spread than the 24× quoted on the [findings](/findings) page,
which uses a 150 TWh floor rather than 10. Same series, different cut, and which
floor to apply is itself a reporting decision rather than a detail.

<Alert status=info>

**So what.** An identical site reports a wildly different Scope 2 figure on
address alone, and under CSRD that figure is audited. Companies buy this table
today from consultancies and from the IEA, whose emission-factor product is
paywalled at four figures.

**Who acts:** sustainability reporting, and site selection long before them.
**Cost of getting it wrong:** a factor of the wrong vintage, or the wrong unit,
inside a number an assurance provider signs.

</Alert>

## The reference table

The current factor for every country that has one. `is_latest_available` is the
filter that produces this cross-section. See the vintage section below for why
it is a filter and not a year.

```sql latest_factors
select
    country_name,
    region,
    year,
    emission_factor_g_co2_per_kwh,
    emission_factor_t_co2_per_mwh,
    low_carbon_share_elec_pct,
    electricity_generation_twh,
    latest_factor_lag_years
from warehouse.grid_emission_factors
where is_latest_available
order by emission_factor_g_co2_per_kwh
```

<DataTable data={latest_factors} rows=12 search=true>
    <Column id=country_name title="Country"/>
    <Column id=year title="Year" fmt="0"/>
    <Column id=emission_factor_g_co2_per_kwh title="gCO₂ / kWh" fmt="#,##0.0"/>
    <Column id=emission_factor_t_co2_per_mwh title="tCO₂e / MWh" fmt="0.0000"/>
    <Column id=low_carbon_share_elec_pct title="Low-carbon %" fmt="0"/>
    <Column id=electricity_generation_twh title="Grid (TWh)" fmt="#,##0.0"/>
    <Column id=latest_factor_lag_years title="Years behind" fmt="0"/>
</DataTable>

Two units for one number, on purpose. `gCO₂/kWh` is how the series is published
and how a reader holds it; `tCO₂e/MWh` is the unit meter data arrives in, and
making a reporter do the divide-by-1000 in a spreadsheet is how a filing acquires
a factor-of-1000 error.

## Vintage: "the latest factor" is not one year

A reporter needs *the most recent published factor for country X*, and that
resolves to a different year for different countries. Filtering the table to a
single latest year would silently drop more than half the world.

```sql vintage
-- Double cast again: the category axis takes a string, and the string has to be
-- made from an integer or it reads '2024.0'.
select
    cast(cast(latest_available_year as integer) as varchar) as vintage_year,
    count(*)                                                as n_countries,
    sum(electricity_generation_twh)                         as twh
from warehouse.grid_emission_factors
where is_latest_available
group by latest_available_year
order by latest_available_year
```

<BarChart
    data={vintage}
    x=vintage_year
    y=n_countries
    swapXY=true
    sort=false
    color="#2a78d6"
    labels=true
    labelFmt="#,##0"
    xAxisTitle="Countries whose newest factor is this year"
    yAxisTitle="Vintage"
/>

Twelve countries are two years or more behind the frontier, and grid size is no
protection: Ukraine's most recent published factor is 2022, on a 111 TWh grid.

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
    <Column id=emission_factor_g_co2_per_kwh title="gCO₂ / kWh" fmt="#,##0.0"/>
    <Column id=electricity_generation_twh title="Grid (TWh)" fmt="#,##0.0"/>
</DataTable>

## Worked example: twelve sites, one year

<Alert status=warning>

**The twelve sites below are invented.** They describe a hypothetical manufacturer on four
continents, seeded in `dbt/seeds/example_scope2_sites.csv`. They are the only
fabricated data in this warehouse. The factors they are multiplied by are real.

</Alert>

```sql group_totals
select
    sum(annual_electricity_mwh)                                        as mwh,
    sum(scope2_t_co2e)                                                 as t_actual,
    sum(scope2_at_best_grid_t_co2e)                                    as t_best,
    sum(scope2_at_worst_grid_t_co2e)                                   as t_worst,
    sum(scope2_at_worst_grid_t_co2e) / sum(scope2_at_best_grid_t_co2e) as ratio,
    count(*)                                                           as n_sites
from warehouse.example_scope2_emissions
```

```sql group_extremes
select
    arg_min(country_name, emission_factor_g_co2_per_kwh) as cleanest_country,
    min(emission_factor_g_co2_per_kwh)                   as cleanest_factor,
    arg_max(country_name, emission_factor_g_co2_per_kwh) as dirtiest_country,
    max(emission_factor_g_co2_per_kwh)                   as dirtiest_factor
from warehouse.example_scope2_emissions
```

<Grid cols=3>
    <BigValue data={group_totals} value=mwh fmt="#,##0" title="Electricity purchased (MWh)"/>
    <BigValue data={group_totals} value=t_actual fmt="#,##0" title="Scope 2, location-based (tCO₂e)"/>
    <BigValue data={group_totals} value=ratio fmt='0.0"×"' title="Best grid vs worst, same demand"/>
</Grid>

```sql sites
select
    site_name,
    site_type,
    country_name,
    annual_electricity_mwh,
    factor_year,
    emission_factor_g_co2_per_kwh,
    scope2_t_co2e,
    share_of_group_pct,
    100 * annual_electricity_mwh / sum(annual_electricity_mwh) over () as share_of_mwh_pct
from warehouse.example_scope2_emissions
order by scope2_t_co2e desc
```

<DataTable data={sites} rows=12>
    <Column id=site_name title="Site"/>
    <Column id=country_name title="Country"/>
    <Column id=annual_electricity_mwh title="MWh / yr" fmt="#,##0"/>
    <Column id=factor_year title="Factor year" fmt="0"/>
    <Column id=emission_factor_g_co2_per_kwh title="gCO₂ / kWh" fmt="#,##0.0"/>
    <Column id=scope2_t_co2e title="tCO₂e" fmt="#,##0"/>
    <Column id=share_of_group_pct title="Share of total" fmt='0.0"%"'/>
</DataTable>

That is the entire calculation: MWh × tCO₂e/MWh, summed. What it produces is a
group total whose shape has almost nothing to do with where the electricity is
used.

```sql site_shares_long
select site_name, 'Share of electricity used' as measure, share_of_mwh_pct as pct
from ${sites}
union all
select site_name, 'Share of emissions reported', share_of_group_pct
from ${sites}
```

<BarChart
    data={site_shares_long}
    x=site_name
    y=pct
    series=measure
    seriesColors={{
        'Share of electricity used': ['#1baf7a', '#199e70'],
        'Share of emissions reported': ['#eda100', '#c98500']
    }}
    type=grouped
    swapXY=true
    sort=false
    yFmt="0"
    xAxisTitle="Site"
    yAxisTitle="Share of group total (%)"
/>

Lyon and Göteborg together draw 17% of the group's electricity and account for
1.7% of its reported emissions. Lyon alone draws three times the power of the
Durban depot, 54 GWh against 18, and reports less than a fifth of its tonnes,
because France's grid runs at 41 gCO₂/kWh and South Africa's at 699. At the other
end of the table, Pune is 11% of the electricity and 18% of the footprint.

```sql scenarios
-- Short labels: the category axis clips long ones from the left, so
-- "All on the group's cleanest grid" renders missing its first character.
select 'As sited today' as scenario, t_actual as t_co2e, 1 as ord from ${group_totals}
union all
select 'All on cleanest grid', t_best, 2 from ${group_totals}
union all
select 'All on dirtiest grid', t_worst, 3 from ${group_totals}
order by ord
```

<BarChart
    data={scenarios}
    x=scenario
    y=t_co2e
    swapXY=true
    sort=false
    color="#eb6834"
    labels=true
    labelFmt="#,##0"
    xAxisTitle="Scope 2, location-based (tCO₂e)"
    yAxisTitle="Scenario"
/>

The same <Value data={group_totals} column=mwh fmt="#,##0"/> MWh, moved nowhere except on paper: every site placed on the cleanest grid in the set, <Value data={group_extremes} column=cleanest_country/> at <Value data={group_extremes} column=cleanest_factor fmt="0.0"/> g/kWh, then every site on the dirtiest, <Value data={group_extremes} column=dirtiest_country/> at <Value data={group_extremes} column=dirtiest_factor fmt="#,##0"/> g/kWh.

Both ends are countries this company already operates in, so the ratio between
them is not hypothetical. It is the accumulated cost of siting decisions
already taken, sitting in a number that has to be published.

## Has the factor been restated?

A disclosure is filed against the factor published *at the time*. When the
publisher revises that year afterwards, the filing does not become wrong. It
becomes a filing against a superseded factor, which is something you have to be
able to demonstrate. `history.snap_grid_emission_factors` is the SCD2 snapshot
that keeps the versions, from 2015 on.

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

That makes the snapshot the one part of this table a rebuild cannot reproduce,
which is why it is carried in rather than recomputed: the
[Pages build](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/blob/main/.github/workflows/pages.yml)
copies `history` out of the most recent
[data release](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/releases)
before it builds, and the release does the same from the release before it. The
[Restatements page](/restatements) is the same mechanism pointed at OWID's CO₂
estimates.

{/if}

## What this factor is not

The three caveats a practitioner checks first. Naming them is not a hedge. A
factor handed over without them is the thing that fails assurance.

<Alert status=warning>

**Location-based only.** This is the grid average where a site sits. A
market-based factor reflects the contracts a company actually holds, such as
RECs, Guarantees of Origin, PPAs and supplier-specific residual mixes, and no
public
dataset carries those. A company reporting both bases will find the two lines
differ substantially, and only this one can be built from open data.

**An annual average, not hourly matching.** A site drawing power overnight on a
wind-heavy grid, or at a summer peak met by gas, is not on the annual mean. 24/7
carbon-free-energy accounting needs hourly generation data; a yearly grain
structurally cannot express it.

**Production-based, not consumption-based.** OWID's series is the carbon
intensity of electricity *generated* in a country. It ignores imports, so a
country running on hydro imported from a neighbour looks cleaner than its
consumed mix, and an exporter of coal power looks dirtier.

</Alert>

One more, from the warehouse rather than the standard: five territories in OWID's
energy data (Guadeloupe, Martinique, Réunion, French Guiana and the Falklands) do
not exist in the country dimension, so they carry no factor here. The dimension is
authoritative for what a country is throughout this warehouse, which is also what
keeps World Bank aggregates like `WLD` out of every fact, and the
[coverage page](/coverage) is where absences are rows.

---

<small>Source: <a href="https://github.com/owid/energy-data">OWID Energy</a>
(<code>carbon_intensity_elec</code>), modelled as
<code>marts.dim_grid_emission_factors</code> and snapshotted as
<code>history.snap_grid_emission_factors</code> (SCD2, <code>check</code>
strategy, 2015 onwards). The GHG Protocol
<a href="https://ghgprotocol.org/scope-2-guidance">Scope 2 Guidance</a> is the
standard this page refers to. Nothing here is advice on how to file; it is the
input to the calculation, with its limits stated.</small>
