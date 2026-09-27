---
title: What it is not
description: The three caveats a practitioner checks first on a location-based Scope 2 factor, and the territories this warehouse gives none.
sidebar_position: 4
---

[← Scope 2 Factors](/scope2)

**Naming these is not a hedge: a factor handed over without them is what fails
assurance.**

<Alert status=warning>

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

The factor is OWID's `carbon_intensity_elec`, modelled as
`marts.dim_grid_emission_factors` and snapshotted as
`history.snap_grid_emission_factors` (SCD2, `check` strategy, 2015 onwards).
