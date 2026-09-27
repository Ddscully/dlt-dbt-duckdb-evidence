---
title: 8. The grid gap
description: The spread of national grid carbon intensity since 2000, across the same eighty countries every year.
sidebar_position: 8
---

[← All nine findings](/findings)

**The average grid got 18% cleaner since 2000. The gap between the cleanest and dirtiest grids closed by only 8%, so the penalty for being at the wrong end is not going away.**

Every other finding is about direction: who fell, who rose, who decoupled. This
one is about *spread*, and it points the other way.

```sql latest_years
select * from warehouse.latest_years
```

```sql grid_percentiles
-- A balanced panel: the same countries in every year from 2000 on, so the
-- spread cannot move merely because the sample did. Without this the country
-- count grows from 82 to 108 over the period and the widening below is partly
-- new reporters arriving, which is not the same claim.
--
-- Grids above 10 TWh only. A 2 TWh grid's intensity swings on one plant opening.
with eligible as (
    select country_iso3
    from warehouse.emissions_energy
    where year between 2000 and (select elec_year from ${latest_years})
      and carbon_intensity_elec_g_kwh is not null
      and electricity_generation_twh > 10
    group by 1
    having count(*) = (select elec_year from ${latest_years}) - 1999
),

stats as (
    select
        year,
        quantile_cont(carbon_intensity_elec_g_kwh, 0.1) as p10,
        quantile_cont(carbon_intensity_elec_g_kwh, 0.5) as p50,
        quantile_cont(carbon_intensity_elec_g_kwh, 0.9) as p90
    from warehouse.emissions_energy
    where country_iso3 in (select country_iso3 from eligible)
      and year between 2000 and (select elec_year from ${latest_years})
    group by year
)

select year, '90th percentile (dirtier grids)' as grid, p90 as g_kwh from stats
union all select year, 'Median', p50 from stats
union all select year, '10th percentile (cleaner grids)', p10 from stats
order by year
```

<LineChart
    data={grid_percentiles}
    x=year
    y=g_kwh
    series=grid
    seriesColors={{
        '90th percentile (dirtier grids)': ['#eb6834', '#d95926'],
        'Median': ['#8a8f98', '#9aa0a8'],
        '10th percentile (cleaner grids)': ['#2a78d6', '#3987e5']
    }}
    xFmt="0"
    yFmt="#,##0"
    yMin=0
    title="National grid carbon intensity, 10th, 50th and 90th percentiles"
    subtitle="The same 80 countries every year, grids above 10 TWh, gCO₂ per kWh"
    yAxisTitle="gCO₂ per kWh"
/>

The three lines fall roughly in parallel, so the distance between them barely
changes. The dirtiest grids have come down by about a tenth, and the cleanest
were already near the floor.

```sql grid_dispersion
with eligible as (
    select country_iso3
    from warehouse.emissions_energy
    where year between 2000 and (select elec_year from ${latest_years})
      and carbon_intensity_elec_g_kwh is not null
      and electricity_generation_twh > 10
    group by 1
    having count(*) = (select elec_year from ${latest_years}) - 1999
)

select
    cast(cast(year as integer) as varchar) as year_label,
    avg(carbon_intensity_elec_g_kwh)       as mean_ci,
    quantile_cont(carbon_intensity_elec_g_kwh, 0.9)
        - quantile_cont(carbon_intensity_elec_g_kwh, 0.1) as gap_p90_p10,
    stddev_samp(carbon_intensity_elec_g_kwh)
        / avg(carbon_intensity_elec_g_kwh) as spread_relative_to_mean
from warehouse.emissions_energy
where country_iso3 in (select country_iso3 from eligible)
  and year in (2000, (select elec_year from ${latest_years}))
group by 1
order by 1
```

<DataTable data={grid_dispersion} rows=2 rowNumbers=false>
    <Column id=year_label title="Year" align=left/>
    <Column id=mean_ci title="Mean gCO₂/kWh" fmt="#,##0"/>
    <Column id=gap_p90_p10 title="Gap, 10th to 90th percentile" fmt="#,##0"/>
    <Column id=spread_relative_to_mean title="Spread relative to mean" fmt="0.00"/>
</DataTable>

Because the mean fell faster than the spread, the spread *relative* to the mean
rose by 23%: in proportional terms the world's grids are further apart than in
2000. Neither reading is an artefact of the start and end points: fitted across
all twenty-five years, the fall in the mean and the rise in relative spread both
carry p-values below 0.001.

<Alert status=info>

**So what.** A transition that improves every grid at roughly the same
proportional rate leaves the ranking intact, and with it the penalty for being at
the wrong end. Anything priced off *where* electricity is consumed (the Scope 2
line of a disclosure, the embedded carbon in an imported tonne, the siting of a
plant) does not get cheaper to get wrong as the world decarbonises. On this
trajectory a location premium is a permanent feature of the next two decades.

**Who acts:** whoever signs off site selection, long-term supply agreements or a
decarbonisation roadmap that assumes convergence. **Cost of getting it wrong:**
building a twenty-year plan on the expectation that the gap closes on its own.

</Alert>
