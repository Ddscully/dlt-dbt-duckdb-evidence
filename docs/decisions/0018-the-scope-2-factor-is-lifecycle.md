# 0018. The Scope 2 factor stays OWID's lifecycle series, labelled as lifecycle, under the column names it has

Status: accepted 2026-09-30 (#121)

## Context

`marts.dim_grid_emission_factors` packages OWID's `carbon_intensity_elec` as a
reference table for the electricity line of a disclosure. Until this record
every description of it, in the ymls, the skill, the docs and the Scope 2 pages,
said the series *is* the GHG Protocol's location-based Scope 2 emission factor.
Three sources, read directly, say otherwise:

- **OWID's codebook** titles the column "Lifecycle carbon intensity of
  electricity generation" and gives its unit as "grams of CO₂ equivalents per
  kilowatt-hour", sourced from Ember's Yearly Electricity Data.
- **Ember's methodology** says the figures "aim to include full lifecycle
  emissions including upstream methane, supply chain and manufacturing
  emissions, and include all gases, converted into CO2 equivalent over a
  100-year timescale". They are computed, not metered: each fuel's generation
  times a factor, from UNECE for coal, nuclear and wind, Jordaan et al. for gas
  (for the year 2017) and IPCC AR5 for the rest (hydro 24 g/kWh, solar 48). It
  describes no generation-only variant.
- **The GHG Protocol Scope 2 Guidance** says both Scope 2 methods "use
  generation-only emission factors", which "do not include T&D losses or
  upstream life-cycle emissions"; those "should be quantified and reported in
  scope 3, category 3". Its hierarchy of location-based factors (table 6.2) asks
  for "combustion-only (direct) GHG emission rates".

So the series is a grid average, which is what the location-based method uses,
on a boundary that method does not use, in a unit the column names do not state:
`emission_factor_g_co2_per_kwh` holds CO2e.

Measured in the warehouse: the seven grids above 10 TWh that are at least 99.5%
low-carbon in their latest year carry 23.1 to 27.8 g/kWh. A generation-only
factor for a grid that burns almost nothing is at or near zero. That is the one
place the difference shows without a second series to compare against.

Measured against a second series: the European Environment Agency publishes a
generation-only intensity for each EU member, which by its own account does "not
take into account life-cycle greenhouse gas emissions" and leaves out upstream
emissions and the combustion of biomass. For 2023 the OWID series reads higher
in 23 of the 27 members, by a median of 34 g/kWh (the median ratio is 1.27), and
14% higher for the 27 weighted by generation, 236 g/kWh against 207. It reads
lower in four: Estonia (396 against 690), Cyprus, Belgium and Lithuania. The two
differ in method as well as in boundary, which is why: the EEA divides the
emissions each country reports by its gross generation, and Ember multiplies
generation by one factor per fuel.

## Decision

- **Every description says lifecycle and CO2e**, and none says the series *is*
  the Scope 2 factor. The Scope 2 pages carry it as the first of four caveats,
  with the low-carbon figure above computed live.
- **`factor_basis` is `location-based, lifecycle`**: the method, then the
  boundary, in the form `fct_cbam_exposure` already uses
  (`location-based, administrative default`). The release notes say the value
  changed.
- **The source stays OWID**, and the column names stay as they are.

## Rejected

- **Renaming `*_g_co2_per_kwh` to `*_g_co2e_per_kwh`.** It is a contract break
  on a shipped table, across two mart models, the snapshot's consumers and every
  Scope 2 page, so it needs a second version and a deprecation window as
  `fct_emissions_energy` had. The number is right and the description now states
  the unit; the name alone did not justify the window.
- **Leaving `factor_basis` as `location-based`.** The column exists so the
  Parquet file carries its own basis when it is detached from these docs, and
  the boundary is the part of the basis a reader would get wrong.
- **The IEA's emission factors**, the generation-only series most filings use.
  They are a paid product under the IEA's terms for non-CC material, which do
  not allow the data, or data derived from it, to be shown to third parties.
  The release is CC BY 4.0 throughout (the same constraint that keeps the CBAM
  annex's IEA factors out).
- **Open generation-only series where a region publishes one** (the EEA, the
  UK's conversion factors, eGRID). Each covers one jurisdiction on its own
  method, so the table would hold two boundaries under one column and the
  spread between countries, which is what the pages chart, would mix them.
- **Stating one size for the gap.** The EEA comparison above is the only
  measurement, it covers one region and one year, and it mixes the boundary with
  the method: the series reads lower than a generation-only one in four
  countries, which a boundary alone cannot do. The pages give that comparison
  as what it is. A figure from the fuel factors alone would be Ember's model
  restated as a finding.

## Consequences

The table is a screening and comparison input, and the pages say it overstates
what a filing would report. A reader who filters on
`factor_basis = 'location-based'` gets no rows from the release that carries
this change. Two things would make it worth revisiting: a global,
generation-only series under a licence the release can carry, which would
arrive as a second factor column beside this one; and a second reason to
version the table, which would be the moment to pay for the rename.
