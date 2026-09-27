---
title: The reference table
description: The current location-based Scope 2 emission factor for every country, in both units, with its year and grid size.
sidebar_position: 1
---

[← Scope 2 Factors](/scope2)

**The current factor for every country that has one, in the unit it is published
in and the unit meter data arrives in.**

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

<DataTable data={latest_factors} rows=20 search=true>
    <Column id=country_name title="Country"/>
    <Column id=year title="Year" fmt="0"/>
    <Column id=emission_factor_g_co2_per_kwh title="gCO₂ / kWh" fmt="#,##0.0" contentType=bar/>
    <Column id=emission_factor_t_co2_per_mwh title="tCO₂e / MWh" fmt="0.0000"/>
    <Column id=low_carbon_share_elec_pct title="Low-carbon %" fmt="0"/>
    <Column id=electricity_generation_twh title="Grid (TWh)" fmt="#,##0.0"/>
    <Column id=latest_factor_lag_years title="Years behind" fmt="0"/>
</DataTable>

Two units for one number, on purpose. `gCO₂/kWh` is how the series is published
and how a reader holds it. `tCO₂e/MWh` is the unit meter data arrives in, and
making a reporter do the divide-by-1000 in a spreadsheet is how a filing acquires
a factor-of-1000 error.

Which year a country's factor belongs to differs from country to country; the
[vintage page](/scope2/vintage) is why.

```sql spread_by_floor
-- The same series cut at two grid-size floors. Which floor to apply is itself a
-- reporting decision: the findings pages use 150 TWh, this site's Scope 2
-- overview 10.
select
    '10 TWh' as floor,
    max(emission_factor_g_co2_per_kwh) / min(emission_factor_g_co2_per_kwh) as ratio,
    count(*) as n_countries
from warehouse.grid_emission_factors
where is_latest_available and electricity_generation_twh > 10
union all
select
    '150 TWh',
    max(emission_factor_g_co2_per_kwh) / min(emission_factor_g_co2_per_kwh),
    count(*)
from warehouse.grid_emission_factors
where is_latest_available and electricity_generation_twh > 150
```

<DataTable data={spread_by_floor} rowNumbers=false>
    <Column id=floor title="Grids larger than"/>
    <Column id=n_countries title="Countries"/>
    <Column id=ratio title="Dirtiest ÷ cleanest" fmt='0"×"'/>
</DataTable>

The spread depends on where the size floor sits: small grids reach both extremes.
Same series, different cut, and the choice belongs in the method note of any
filing that quotes one.

<Alert status=info>

**So what.** An identical site reports a very different Scope 2 figure on address
alone, and under CSRD that figure is audited. Companies buy this table today from
consultancies and from the IEA, whose emission-factor product is paywalled at four
figures.

**Who acts:** sustainability reporting, and site selection long before them.
**Cost of getting it wrong:** a factor of the wrong vintage, or the wrong unit,
inside a number an assurance provider signs.

</Alert>
