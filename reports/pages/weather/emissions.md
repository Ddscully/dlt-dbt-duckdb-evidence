---
title: Emissions
description: Heating demand against CO₂ across the European panel and country by country, pandemic years excluded.
sidebar_position: 2
---

[← Weather](/weather)

**A cold winter changes how much gas a country burns for heat, not what a regulated tariff charges. Tested on what countries emitted, the weather explains much of the year-to-year swing.**

```sql emissions_by_year
-- Heating demand is the average change across the capitals; CO₂ is the panel's
-- total change (sum over sum), so a large emitter counts for what it emits.
with by_year as (
    select
        year,
        avg(hdd_change)                                     as hdd_change,
        100.0 * (sum(co2_mt) / sum(previous_co2_mt) - 1)     as co2_change
    from warehouse.weather_emissions_pairs
    where co2_change is not null
    group by year
)

select cast(cast(year as integer) as varchar) as year_label, 'Heating demand' as measure, hdd_change as pct, year
from by_year
union all
select cast(cast(year as integer) as varchar), 'CO₂ emitted', co2_change, year
from by_year
order by year, measure desc
```

```sql emissions_fit
-- The pandemic years are left out of every figure here: 2020 was a mild winter
-- and a lockdown, 2021 a cold one and a rebound, and both line up with the
-- weather by coincidence. Left in, they flatter the fit.
with by_year as (
    select
        year,
        avg(hdd_change)                                     as hdd_change,
        100.0 * (sum(co2_mt) / sum(previous_co2_mt) - 1)     as co2_change
    from warehouse.weather_emissions_pairs
    where co2_change is not null
      and not is_pandemic_year
    group by year
)

select
    count(*)                                as n_year_pairs,
    corr(hdd_change, co2_change)            as correlation,
    100 * regr_r2(co2_change, hdd_change)   as variance_explained,
    10 * regr_slope(co2_change, hdd_change) as co2_pct_per_10pct_colder
from by_year
```

```sql country_weather_fit
-- Countries emitting at least 10 Mt in their latest paired year: below that, a
-- single plant's outage moves the national figure more than a winter does.
with fit as (
    select
        country_name,
        count(*)                                        as n_year_pairs,
        corr(hdd_change, co2_change)                    as correlation,
        corr(hdd_change, gas_co2_change)                as gas_correlation,
        10 * regr_slope(co2_change, hdd_change)         as co2_pct_per_10pct_colder,
        arg_max(co2_mt, year)                           as latest_co2_mt
    from warehouse.weather_emissions_pairs
    where co2_change is not null
      and not is_pandemic_year
    group by country_name
    having count(*) >= 8
)

select *
from fit
where latest_co2_mt >= 10
order by correlation desc
```

```sql country_fit_summary
with fit as (
    select
        country_name,
        corr(hdd_change, co2_change)                    as correlation,
        10 * regr_slope(co2_change, hdd_change)         as co2_pct_per_10pct_colder,
        arg_max(co2_mt, year)                           as latest_co2_mt
    from warehouse.weather_emissions_pairs
    where co2_change is not null
      and not is_pandemic_year
    group by country_name
    having count(*) >= 8
)

select
    count(*)                                                        as n_countries,
    count(*) filter (where correlation >= 0.5)                      as n_strong,
    count(*) filter (where correlation > 0)                         as n_positive,
    median(co2_pct_per_10pct_colder) filter (where correlation >= 0.5) as typical_slope
from fit
where latest_co2_mt >= 10
```

{#if emissions_fit[0].n_year_pairs >= 5}

<Grid cols=3>
    <BigValue data={emissions_fit} value=correlation fmt='0.00' title="Correlation, heating demand vs CO₂"/>
    <BigValue data={emissions_fit} value=variance_explained fmt='0"%"' title="Of the CO₂ swing explained by weather"/>
    <BigValue data={emissions_fit} value=n_year_pairs title="Year-pairs, pandemic excluded"/>
</Grid>

<BarChart
    data={emissions_by_year}
    x=year_label
    y=pct
    series=measure
    type=grouped
    sort=false
    seriesColors={{
        'Heating demand': ['#2a78d6', '#3987e5'],
        'CO₂ emitted': ['#eb6834', '#d95926']
    }}
    yFmt='0"%"'
    title="Change on the year before, across the panel"
    subtitle="Heating demand averaged over the capitals; CO₂ is the panel total. 2020 and 2021 are the pandemic years."
/>

Leaving out the two pandemic years, the panel's change in CO₂ follows its change in heating demand with a correlation of <Value data={emissions_fit} column=correlation fmt='0.00'/> across <Value data={emissions_fit} column=n_year_pairs/> year-pairs, so the weather alone accounts for <Value data={emissions_fit} column=variance_explained fmt='0"%"'/> of the year-to-year swing in what these countries emit. On the fitted slope a winter 10% colder than the one before adds about <Value data={emissions_fit} column=co2_pct_per_10pct_colder fmt='0.0"%"'/> to the year's CO₂ across the panel.

The chart shows it without the statistics. The warm winter of 2014 took heating
demand down by about a sixth and the panel's emissions fell with it; the colder
2015 pushed demand back up by an eighth and emissions rose. The years that break
the pattern are years something else happened, and they are worth reading as
exceptions: in 2019 the panel's coal emissions fell by about a seventh while
heating demand barely moved.

<BarChart
    data={country_weather_fit}
    x=country_name
    y=correlation
    swapXY=true
    sort=false
    yMin={-1}
    yMax={1}
    yFmt='0.00'
    color="#2a78d6"
    title="Heating demand against CO₂, country by country"
    subtitle="Correlation of the year-over-year changes; countries emitting 10 Mt or more, pandemic years excluded."
/>

Country by country, <Value data={country_fit_summary} column=n_positive/> of the <Value data={country_fit_summary} column=n_countries/> countries emitting 10 Mt or more move with their winters, and <Value data={country_fit_summary} column=n_strong/> of them at a correlation of 0.5 or better, where a winter 10% colder than the last typically adds <Value data={country_fit_summary} column=typical_slope fmt='0.0"%"'/> to national CO₂.

The bottom of the chart is where there is little to heat, as in Spain and
Portugal; where heat is already electric and the electricity is hydro, as in
Norway; or where growth swamps the winter, as in Türkiye, whose emissions have
nearly doubled since 2005. The table below adds the gas line on its own, which is
the fuel a winter mostly moves.

<DataTable data={country_weather_fit} rows=12>
    <Column id=country_name title="Country"/>
    <Column id=correlation title="Correlation, CO₂" fmt='0.00'/>
    <Column id=gas_correlation title="Correlation, gas CO₂" fmt='0.00'/>
    <Column id=co2_pct_per_10pct_colder title="CO₂ per 10% colder winter" fmt='0.0"%"'/>
    <Column id=latest_co2_mt title="CO₂, latest (Mt)" fmt='#,##0'/>
</DataTable>

Ten or so year-pairs is a short series, and any one country's correlation carries
wide error: at that length, about 0.63 is where a correlation stops being
distinguishable from chance at the conventional 5% level. The evidence is the
pattern rather than any single bar. Nearly every country leans the same way, and
the lean is strongest where winters are cold and heating burns gas.

<Alert status=info>

**So what.** When somebody attributes an emissions movement to policy,
efficiency or fuel switching, "it was a milder year" is the cheapest competing
explanation and usually the one nobody checks. For emissions it is often most of
the answer: a country or a company whose footprint is mostly heating fuel can
report a cut of a few percent after a mild winter and have reported the weather.
Normalising a year-on-year comparison for degree days, or fitting it the way
this section does, is routine in energy management, and an emissions figure
needs it for the same reason.

**Who acts:** whoever reports year-on-year emissions progress, and whoever
assures it. **Cost of getting it wrong:** crediting a warm winter to a
programme, then explaining the "reversal" the next time the winter is cold.

</Alert>

{:else}

The weather archive holds too few consecutive complete years with published
emissions to fit a relationship yet. OWID's CO₂ runs a year behind the weather,
so the newest complete weather year always waits a year for its pair. A published
release carries the archive forward rather than refetching it, so this section
fills in as the archive deepens.

{/if}
