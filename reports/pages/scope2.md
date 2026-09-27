---
title: Scope 2 Factors
description: Grid emission factors packaged as a reference table, with the vintage, the lineage and a worked example of the disclosure line they feed.
sidebar_position: 2
---

The **location-based Scope 2 emission factor** is the number a company multiplies
its metered electricity by for the purchased-electricity line of a CSRD, SECR or
CDP disclosure. This is that factor for every country, with its vintage and its
revision history.

```sql headline
-- The double cast is not redundant. Evidence's DuckDB extractor writes every
-- numeric column to parquet as DOUBLE, so a page-level `cast(year as varchar)`
-- runs over a double and produces '2025.0'. See reports/README.md.
select
    count(*)                                                as n_countries,
    cast(cast(max(latest_available_year) as integer) as varchar) as newest_vintage,
    count(*) filter (where latest_factor_lag_years = 0)      as n_at_frontier
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

## The same site reports a different number on address alone

```sql factor_map
-- Capped at 900 g for the colour, so a few diesel islands above 1,000 do not
-- wash out the continents; the tooltip shows the real factor.
select
    country_iso3,
    country_name,
    emission_factor_g_co2_per_kwh,
    cast(cast(year as integer) as varchar)          as factor_year,
    least(emission_factor_g_co2_per_kwh, 900)       as "gCO₂ per kWh"
from warehouse.grid_emission_factors
where is_latest_available
```

<!-- `../`, because this page is served at `<base>/<page>/` and the two files sit
at the site root; a bare name resolves against the page and 404s. -->
<AreaMap
    data={factor_map}
    areaCol=country_iso3
    geoJsonUrl="../world-countries.geojson"
    geoId=iso3
    value="gCO₂ per kWh"
    valueFmt="#,##0"
    colorPalette={['#d6e6f7', '#8fb8e6', '#eda100', '#b5530a', '#5a2403']}
    basemap="../blank-tile.png"
    startingLat=30
    startingLong=10
    startingZoom=1
    height=420
    title="Latest grid emission factor, gCO₂ per kWh (900 or more shown as 900)"
    tooltip={[
        {id: 'country_name', showColumnName: false, valueClass: 'font-semibold'},
        {id: 'emission_factor_g_co2_per_kwh', title: 'gCO₂ per kWh', fmt: '#,##0'},
        {id: 'factor_year', title: 'Year'}
    ]}
/>

<style>
    :global(.leaflet-container) { background: transparent !important; }
</style>

Among grids over 10 TWh the factor runs from <Value data={spread} column=cleanest_country/> at <Value data={spread} column=cleanest fmt="0.0"/> g per kWh to <Value data={spread} column=dirtiest_country/> at <Value data={spread} column=dirtiest fmt="#,##0"/> g. Under CSRD that figure is audited, and companies buy this table from consultancies and the IEA. **[The reference table →](/scope2/factors)**

## "The latest factor" is not one year

```sql vintage
-- Double cast again: the category axis takes a string, and the string has to be
-- made from an integer or it reads '2024.0'. Cumulative *descending*, because
-- "cut at year X" means "accept a factor from X or later".
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
    title="Countries by year of their newest factor, and the coverage of each cut-off"
    yAxisTitle="Countries"
    y2AxisTitle="Covered by this cut-off"
/>

Insisting on this year's factor covers under half the world; accepting last year's
covers almost all of it. The cut-off is a choice about how much of the world a
filing leaves out. **[Vintage and restatements →](/scope2/vintage)**

## Where the power is used is not where the emissions land

<Alert status=warning>

**The twelve sites here are invented**, the only fabricated data in this
warehouse. The factors they are multiplied by are real.

</Alert>

```sql site_gap
select
    site_name,
    share_of_group_pct
        - 100 * annual_electricity_mwh / sum(annual_electricity_mwh) over () as gap_pts,
    case
        when share_of_group_pct
            > 100 * annual_electricity_mwh / sum(annual_electricity_mwh) over ()
            then 'More of the emissions than of the power'
        else 'Less of the emissions than of the power'
    end as direction
from warehouse.example_scope2_emissions
order by gap_pts desc
```

<BarChart
    data={site_gap}
    x=site_name
    y=gap_pts
    series=direction
    seriesColors={{
        'More of the emissions than of the power': ['#eb6834', '#d95926'],
        'Less of the emissions than of the power': ['#2a78d6', '#3987e5']
    }}
    swapXY=true
    sort=false
    yFmt='0" pts"'
    title="Each site's share of reported emissions minus its share of electricity used"
    subtitle="Percentage points, one hypothetical manufacturer"
/>

Lyon and Göteborg draw 17% of the group's electricity and report under 2% of its
emissions; Pune, Katowice and Suzhou report far more than they draw. The
calculation is only MWh times the factor; the grid does the rest. **[The worked example →](/scope2/worked-example)**

<Alert status=warning>

**Location-based, annual and production-based.** No contracts, no hourly
matching, no electricity trade: each is a reason this factor differs from what a
company's market-based line or a 24/7 claim would say. **[What the factor is not →](/scope2/limits)**

</Alert>

<small>Source: <a href="https://github.com/owid/energy-data">OWID Energy</a>
(<code>carbon_intensity_elec</code>), modelled as
<code>marts.dim_grid_emission_factors</code> and snapshotted as
<code>history.snap_grid_emission_factors</code>. The GHG Protocol
<a href="https://ghgprotocol.org/scope-2-guidance">Scope 2 Guidance</a> is the
standard this page refers to. Nothing here is advice on how to file.</small>
