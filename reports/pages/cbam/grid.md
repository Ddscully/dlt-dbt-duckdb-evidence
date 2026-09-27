---
title: The grid
description: Why a country's electricity grid barely enters its CBAM border cost, measured by product group and on primary aluminium.
sidebar_position: 2
---

[← CBAM Exposure](/cbam)

**Electricity is under 1% of the carbon the annex prices, so a country's grid
barely moves its border cost. The reason is the regulation, not the statistics:
it mostly does not count electricity at all.**

```sql electricity_share
-- Country-specific values only: a row that copies the catch-all value is one
-- number repeated, 471 times for cement, and would weight the averages towards it.
select
    product_group,
    avg(direct_t_co2e_per_t)                                    as avg_direct,
    avg(indirect_t_co2e_per_t)                                  as avg_indirect,
    100.0 * sum(coalesce(indirect_t_co2e_per_t, 0))
        / sum(total_t_co2e_per_t)                               as pct_electricity,
    case
        when count(indirect_t_co2e_per_t) = 0        then 'Not published'
        when count(indirect_t_co2e_per_t) = count(*) then 'Every row'
        else count(indirect_t_co2e_per_t)::varchar
             || ' of ' || count(*)::varchar || ' rows'
    end                                                         as indirect_coverage
from warehouse.cbam_exposure
where is_country_specific
  and not is_fallback_table
  and total_t_co2e_per_t > 0
group by 1
order by pct_electricity desc
```

```sql electricity_overall
select
    100.0 * sum(coalesce(indirect_t_co2e_per_t, 0))
        / sum(total_t_co2e_per_t)  as pct_electricity_overall,
    count(*)                       as n_rows
from warehouse.cbam_exposure
where is_country_specific
  and not is_fallback_table
  and total_t_co2e_per_t > 0
```

<Grid cols=2>
    <BigValue data={electricity_overall} value=pct_electricity_overall fmt='0.0"%"' title="Electricity's share of all priced carbon"/>
    <BigValue data={electricity_overall} value=n_rows fmt="#,##0" title="Country × good values priced"/>
</Grid>

<DataTable data={electricity_share} rows=5 rowNumbers=false>
    <Column id=product_group title="Product group"/>
    <Column id=avg_direct title="Direct tCO₂e/t" fmt='0.00'/>
    <Column id=avg_indirect title="Indirect (electricity) tCO₂e/t" fmt='0.00'/>
    <Column id=pct_electricity title="Electricity's share" fmt='0.0"%"'/>
    <Column id=indirect_coverage title="Indirect value published for" align=left/>
</DataTable>

Indirect emissions, the carbon in the electricity the plant drew, are published
only for **cement and fertilisers**. For aluminium and hydrogen the annex carries
no indirect column at all, and for iron and steel it is present on 27 of the
6,229 country-specific rows. Where it does count it is small: 5.8% of a cement
tonne and 5.5% of a fertiliser one.

## Primary aluminium: the border cost against the grid

```sql primary_aluminium
-- Route K is primary aluminium, made by electrolysis; L, the other route the
-- annex prints for this good, is secondary aluminium from scrap. Holding the
-- route fixed keeps it out of the comparison, which matters because in steel
-- the route is the whole spread.
select
    country_display_name,
    grid_factor_t_co2_per_mwh,
    grid_factor_year,
    cbam_cost_2026_eur_per_t
from warehouse.cbam_exposure
where good_key = '7601-unwrought-aluminium'
  and is_country_specific
  and not is_fallback_table
  and production_route_code = 'K'
  and grid_factor_t_co2_per_mwh is not null
```

```sql primary_aluminium_summary
select
    count(*)                                                            as n_countries,
    corr(grid_factor_t_co2_per_mwh, cbam_cost_2026_eur_per_t)            as correlation,
    arg_min(country_display_name, grid_factor_t_co2_per_mwh)            as cleanest_grid_country,
    arg_min(cbam_cost_2026_eur_per_t, grid_factor_t_co2_per_mwh)        as cleanest_grid_cost,
    arg_max(country_display_name, grid_factor_t_co2_per_mwh)            as dirtiest_grid_country,
    arg_max(cbam_cost_2026_eur_per_t, grid_factor_t_co2_per_mwh)        as dirtiest_grid_cost
from warehouse.cbam_exposure
where good_key = '7601-unwrought-aluminium'
  and is_country_specific
  and not is_fallback_table
  and production_route_code = 'K'
  and grid_factor_t_co2_per_mwh is not null
```

<ScatterPlot
    data={primary_aluminium}
    x=grid_factor_t_co2_per_mwh
    y=cbam_cost_2026_eur_per_t
    xFmt="0.00"
    yFmt='€#,##0'
    yMin={0}
    color="#b5530a"
    xAxisTitle="Grid emission factor (tCO₂ / MWh)"
    yAxisTitle="CBAM cost per tonne, 2026"
    tooltipTitle=country_display_name
    title="Primary aluminium: border cost against the grid it was smelted on"
    subtitle="Unwrought aluminium, production route K, one point per sourcing country"
/>

Primary aluminium is made by electrolysis, and the annex publishes no indirect value for it. Across the <Value data={primary_aluminium_summary} column=n_countries/> countries with a primary-aluminium value, <Value data={primary_aluminium_summary} column=cleanest_grid_country/> has the cleanest grid and pays <Value data={primary_aluminium_summary} column=cleanest_grid_cost fmt='€#,##0'/> a tonne while <Value data={primary_aluminium_summary} column=dirtiest_grid_country/> has the dirtiest and pays <Value data={primary_aluminium_summary} column=dirtiest_grid_cost fmt='€#,##0'/> a tonne, with a correlation of <Value data={primary_aluminium_summary} column=correlation fmt="0.00"/> between the two.

## Every product group against the grid

```sql grid_vs_default
-- The grid factor is OWID's (`grid_factor_t_co2_per_mwh`), context rather than
-- the factor the annex used, which is the question being asked: does the grid a
-- country runs on show up in what its goods pay at all?
select
    product_group,
    count(*)                                               as n_values,
    corr(grid_factor_t_co2_per_mwh, total_t_co2e_per_t)     as correlation
from warehouse.cbam_exposure
where is_country_specific
  and not is_fallback_table
  and grid_factor_t_co2_per_mwh is not null
group by product_group
order by correlation desc
```

<DataTable data={grid_vs_default} rows=5 rowNumbers=false>
    <Column id=product_group title="Product group"/>
    <Column id=n_values title="Country × good values" fmt="#,##0"/>
    <Column id=correlation title="Correlation with the country's grid factor" fmt="0.00"/>
</DataTable>

Iron and steel and cement follow the grid somewhat. For steel the priced value is
almost entirely direct emissions, so whatever links a country's steel to its grid,
it is not the carbon in the electricity. Aluminium does not follow the grid at all.

<Alert status=info>

**So what.** The bill is overwhelmingly the carbon burned *in the process*, the
coke in a blast furnace and the calcination of limestone, not the carbon behind
the meter. A grid factor is the right input to a [Scope 2](/scope2) disclosure and
the wrong input to a sourcing decision on steel.

**Who acts:** whoever is building a supplier-screening or carbon-cost model.
**Cost of getting it wrong:** ranking suppliers on grid data that the border cost
is almost entirely insensitive to.

</Alert>
