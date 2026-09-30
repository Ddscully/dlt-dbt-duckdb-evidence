---
title: What it is not
description: The four caveats a practitioner checks first on a location-based Scope 2 factor, and the territories this warehouse gives none.
sidebar_position: 4
---

[← Scope 2 Factors](/scope2)

**Naming these is not a hedge: a factor handed over without them is what fails
assurance.**

```sql low_carbon_floor
-- Grids that burn almost nothing, which a generation-only factor would put at or
-- near zero. Above 10 TWh, as on the overview: the only zeros in the series are
-- on grids of a fraction of a TWh.
select
    count(*)                           as n_grids,
    min(emission_factor_g_co2_per_kwh) as lowest,
    max(emission_factor_g_co2_per_kwh) as highest
from warehouse.grid_emission_factors
where is_latest_available
  and electricity_generation_twh > 10
  and low_carbon_share_elec_pct >= 99.5
```

<Alert status=warning>

**Lifecycle, not generation-only, and CO₂e.** The Scope 2 methods use factors
assessed at the point of generation. What happens upstream of the plant
(extracting and moving the fuel, methane leaks, building the plant) is reported
in Scope 3, category 3. This series includes it: it is Ember's estimate, each
fuel's generation multiplied by a lifecycle factor, in CO₂ equivalents. So it
reads higher than the factor the standard describes, and a company that also
reports category 3 would count the upstream part twice. The clean end shows it:
the <Value data={low_carbon_floor} column=n_grids/> grids above 10 TWh that are at least 99.5% low-carbon carry <Value data={low_carbon_floor} column=lowest fmt="0.0"/> to <Value data={low_carbon_floor} column=highest fmt="0.0"/> g per kWh, where a generation-only factor would be at or near zero.

One open series does count generation only, for one region: the
[European Environment Agency's](https://www.eea.europa.eu/en/analysis/indicators/greenhouse-gas-emission-intensity-of-1)
for the EU. For 2023 this series reads higher than it in 23 of the 27 members,
by a median of 34 g per kWh, and 14% higher for the 27 together. It reads lower
in four, Estonia most of all, because the two differ in method as well as in
boundary: the EEA divides the emissions a country reports by what it generates,
and Ember multiplies generation by a factor for each fuel.

**Location-based only.** This is the grid average where a site sits. A
market-based factor reflects the contracts a company actually holds, such as
RECs, Guarantees of Origin, PPAs and supplier-specific residual mixes, and no
public dataset carries those. A company reporting both bases will find the two
lines differ substantially, and only this one can be built from open data.

**An annual average, not hourly matching.** A site drawing power overnight on a
wind-heavy grid, or at a summer peak met by gas, is not on the annual mean. 24/7
carbon-free-energy accounting needs hourly generation data; a yearly grain
structurally cannot express it.

**Production-based, not consumption-based.** OWID's series is the carbon
intensity of electricity *generated* in a country. It ignores trade, so a country
that imports much of its power is assigned only what it generates itself: one
importing a neighbour's hydro looks dirtier than the mix it consumes, and one
importing coal power looks cleaner.

</Alert>

One more, from the warehouse rather than the standard: five territories in OWID's
energy data (Guadeloupe, Martinique, Réunion, French Guiana and the Falklands) are
not countries in this warehouse's country list, so they carry no factor here. That
list is what counts as a country throughout, and it is also what keeps World Bank
groupings like "World" and "European Union" out of every total. The
[coverage page](/coverage) is where absences are rows.

The factor is OWID's `carbon_intensity_elec`, which OWID takes from
[Ember's Yearly Electricity Data](https://ember-energy.org/data/yearly-electricity-data/)
and titles "Lifecycle carbon intensity of electricity generation", modelled as
`marts.dim_grid_emission_factors` and snapshotted as
`history.snap_grid_emission_factors` (SCD2, `check` strategy, 2015 onwards).
