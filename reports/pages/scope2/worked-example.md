---
title: Worked example
description: Twelve invented sites of one manufacturer, multiplied by real grid factors, to show how a location-based Scope 2 total is built and what siting does to it.
sidebar_position: 3
---

[← Scope 2 Factors](/scope2)

**One year of a hypothetical manufacturer's electricity, on four continents. The
calculation is MWh times the factor, and the result has almost nothing to do with
where the electricity is used.**

<Alert status=warning>

**The twelve sites below are invented.** They are seeded in
`dbt/seeds/example_scope2_sites.csv` and are the only fabricated data in this
warehouse. The factors they are multiplied by are real.

</Alert>

```sql group_totals
select
    sum(annual_electricity_mwh)                                        as mwh,
    sum(scope2_t_co2e)                                                 as t_actual,
    sum(scope2_at_best_grid_t_co2e)                                    as t_best,
    sum(scope2_at_worst_grid_t_co2e)                                   as t_worst,
    sum(scope2_at_worst_grid_t_co2e) / sum(scope2_at_best_grid_t_co2e) as ratio
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
    <BigValue data={group_totals} value=ratio fmt='0.0"×"' title="Dirtiest grid vs cleanest, same demand"/>
</Grid>

```sql site_shares_long
with sites as (
    select
        site_name,
        share_of_group_pct,
        100 * annual_electricity_mwh / sum(annual_electricity_mwh) over () as share_of_mwh_pct
    from warehouse.example_scope2_emissions
)
select site_name, 'Share of electricity used' as measure, share_of_mwh_pct as pct, share_of_group_pct as ord from sites
union all
select site_name, 'Share of emissions reported', share_of_group_pct, share_of_group_pct from sites
order by ord desc
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
    yFmt='0"%"'
    title="Each site's share of the group's electricity and of its Scope 2 total"
/>

Lyon and Göteborg together draw 17% of the group's electricity and account for
1.7% of its reported emissions. Lyon alone draws three times the power of the
Durban depot, 54 GWh against 18, and reports less than a fifth of its tonnes,
because France's grid runs at 41 gCO₂/kWh and South Africa's at 699. At the other
end, Pune is 11% of the electricity and 18% of the footprint.

## The same demand on the cleanest and dirtiest grid

```sql scenarios
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
    chartAreaHeight=140
    title="Scope 2, location-based (tCO₂e)"
/>

The same <Value data={group_totals} column=mwh fmt="#,##0"/> MWh, moved nowhere except on paper: every site placed on the cleanest grid in the set, <Value data={group_extremes} column=cleanest_country/> at <Value data={group_extremes} column=cleanest_factor fmt="0.0"/> g/kWh, then every site on the dirtiest, <Value data={group_extremes} column=dirtiest_country/> at <Value data={group_extremes} column=dirtiest_factor fmt="#,##0"/> g/kWh.

Both ends are countries this company already operates in, so the ratio between
them is not hypothetical. It is the accumulated cost of siting decisions already
taken, sitting in a number that has to be published.

## The data

```sql sites
select
    site_name,
    site_type,
    country_name,
    annual_electricity_mwh,
    factor_year,
    emission_factor_g_co2_per_kwh,
    scope2_t_co2e,
    share_of_group_pct
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
