---
title: Coverage
description: What the warehouse knows, what it doesn't, and where the two facts differ by column.
sidebar_position: 8
sidebar_badge: Method
---

The four sources disagree about which countries exist, which years they cover,
and how far forward they run. That is why every chart on the other pages filters
for the columns it needs, and why those filters are not interchangeable.

```sql latest_years
select * from warehouse.latest_years
```

## How many countries each series actually covers

```sql coverage_by_year
-- Six series, short labels: eight paginates the legend, which makes half the
-- lines unidentifiable. The rest are in the table below.
--
-- Capped at the latest electricity year rather than max(year), because the very
-- last year in the mart carries Eurostat prices and almost nothing else. Every
-- line dropping to zero on the same tick reads as a rendering fault and drowns
-- out the two real features. Where each series *ends* is the `To` column below.
with bounded as (
    select * from warehouse.emissions_energy
    where year between 1960 and (select elec_year from ${latest_years})
)

select year, 'CO₂' as series, count(co2_mt) as n_countries from bounded group by year
union all
select year, 'GDP', count(gdp_constant_usd) from bounded group by year
union all
select year, 'Electricity mix', count(carbon_intensity_elec_g_kwh) from bounded group by year
union all
select year, 'Primary energy', count(primary_energy_twh) from bounded group by year
union all
select year, 'Consumption CO₂', count(consumption_co2) from bounded group by year
union all
select year, 'Renewables share', count(renewables_share_pct) from bounded group by year
order by series, year
```

<LineChart
    data={coverage_by_year}
    x=year
    y=n_countries
    series=series
    seriesColors={{
        'CO₂': ['#2a78d6', '#3987e5'],
        'GDP': ['#eb6834', '#d95926'],
        'Electricity mix': ['#1baf7a', '#199e70'],
        'Primary energy': ['#eda100', '#c98500'],
        'Consumption CO₂': ['#e87ba4', '#d55181'],
        'Renewables share': ['#4a3aa7', '#9085e9']
    }}
    yAxisTitle="Countries reporting"
    title="Countries reporting each series, by year"
/>

Renewables share stops at 79 countries where the electricity mix covers about
210, primary energy drops by two thirds in its latest year, and the series end in
different years. Each has bitten a query here, and each is why a chart filters for
exactly the columns it needs.

<Details title="The three features in detail">

1. **The renewables ceiling.** `renewables_share_pct` flatlines at 79 countries
   in every year since 1990. OWID's broad-coverage series is the *electricity*
   mix, not the primary-energy mix, which is why the findings page and the country explorer
   use `carbon_intensity_elec_g_kwh` and `low_carbon_share_elec_pct` (about 210
   countries) wherever coverage matters.
2. **The last-year cliff.** `primary_energy_twh` falls off a cliff in the most
   recent year, from ~210 countries to 79. Anything cut to "the latest year"
   without checking loses two thirds of its sample; the findings page reads a
   per-metric latest year from `sources/warehouse/latest_years.sql` for exactly
   this reason.
3. **The sources end at different times.** Read the `To` column below rather
   than the chart, which is capped for legibility. GDP and EU electricity prices
   already run a year beyond the emissions series, with the grid series partly
   there, while consumption-based CO₂ ends a year before the emissions series. The mart's
   `max(year)` is the leader, not the consensus.

</Details>

```sql column_coverage
select 'co2_mt' as column_name, count(distinct country_iso3) as countries,
       min(year) as first_year, max(year) as last_year, count(*) as rows_present
from warehouse.emissions_energy where co2_mt is not null
union all select 'consumption_co2', count(distinct country_iso3), min(year), max(year), count(*)
from warehouse.emissions_energy where consumption_co2 is not null
union all select 'primary_energy_twh', count(distinct country_iso3), min(year), max(year), count(*)
from warehouse.emissions_energy where primary_energy_twh is not null
union all select 'renewables_share_pct', count(distinct country_iso3), min(year), max(year), count(*)
from warehouse.emissions_energy where renewables_share_pct is not null
union all select 'carbon_intensity_elec_g_kwh', count(distinct country_iso3), min(year), max(year), count(*)
from warehouse.emissions_energy where carbon_intensity_elec_g_kwh is not null
union all select 'gdp_constant_usd', count(distinct country_iso3), min(year), max(year), count(*)
from warehouse.emissions_energy where gdp_constant_usd is not null
union all select 'life_expectancy', count(distinct country_iso3), min(year), max(year), count(*)
from warehouse.emissions_energy where life_expectancy is not null
union all select 'poverty_rate', count(distinct country_iso3), min(year), max(year), count(*)
from warehouse.emissions_energy where poverty_rate is not null
union all select 'electricity_price_eur_kwh', count(distinct country_iso3), min(year), max(year), count(*)
from warehouse.emissions_energy where electricity_price_eur_kwh is not null
order by countries desc
```

<DataTable data={column_coverage} rows=10>
    <Column id=column_name title="Column"/>
    <Column id=countries title="Countries" fmt="0" contentType=bar/>
    <Column id=first_year title="From" fmt="0"/>
    <Column id=last_year title="To" fmt="0"/>
    <Column id=rows_present title="Rows" fmt="#,##0"/>
</DataTable>

## Two different kinds of gap

```sql spine_summary
select
    (select count(*) from warehouse.country_year_spine)  as spine_rows,
    (select count(*) from warehouse.emissions_energy)    as fact_rows,
    (select count(distinct country_iso3) from warehouse.country_year_spine) as countries,
    (select count(*) from warehouse.country_year_spine)
        - (select count(*) from warehouse.emissions_energy) as unreported
```

<Grid cols=4>
    <BigValue data={spine_summary} value=countries title="Countries in the dimension"/>
    <BigValue data={spine_summary} value=spine_rows fmt="#,##0" title="Possible country-years"/>
    <BigValue data={spine_summary} value=fact_rows fmt="#,##0" title="At least one source reports"/>
    <BigValue data={spine_summary} value=unreported fmt="#,##0" title="No source reports"/>
</Grid>

The fact is built on a **spine**, `marts.dim_country_year`: every country crossed
with every year. Left-join the fact onto it and a gap comes back as a row you can
count.

### A country-year no source reports at all

```sql gap_by_era
select
    case
        when s.year < 1900 then 'Before 1900'
        when s.year < 1950 then '1900–1949'
        when s.year < 1990 then '1950–1989'
        else '1990 onwards'
    end as era,
    case
        when s.year < 1900 then 1
        when s.year < 1950 then 2
        when s.year < 1990 then 3
        else 4
    end as ord,
    count(*) as unreported_country_years
from warehouse.country_year_spine s
left join warehouse.emissions_energy f
    on s.country_iso3 = f.country_iso3 and s.year = f.year
where f.country_iso3 is null
group by era, ord
order by ord
```

<BarChart
    data={gap_by_era}
    x=era
    y=unreported_country_years
    sort=false
    color="#eb6834"
    labels=true
    labelFmt="#,##0"
    xAxisTitle="Era"
    title="Country-years no source reports"
/>

Almost all are the deep past: the spine starts in 1750 because OWID's emissions
series does. From 1990 on it is essentially complete, so this gap is about
history, not the pipeline. These rows exist only in `dim_country_year`.

### A country-year in the fact where one source is silent

These reach the fact with nulls where the silent source would be, and a `where`
clause on the wrong column drops them without saying so.

**World Bank data, no OWID emissions:** small territories OWID does not estimate
for.

```sql wb_no_owid
select country_name, region, income_group, population, life_expectancy
from warehouse.emissions_energy
where year = (select co2_year from ${latest_years})
  and co2_mt is null
  and population is not null
order by population desc
```

<DataTable data={wb_no_owid} rows=8>
    <Column id=country_name title="Country / territory"/>
    <Column id=region title="Region"/>
    <Column id=income_group title="Income group"/>
    <Column id=population title="Population" fmt="#,##0"/>
</DataTable>

**OWID emissions, no World Bank GDP:** absent from every intensity and
decoupling measure on this site. Taiwan, not a World Bank member, emits more than
the Netherlands.

```sql owid_no_wb
select country_name, region, co2_mt, population
from warehouse.emissions_energy
where year = (select co2_year from ${latest_years})
  and co2_mt is not null
  and gdp_constant_usd is null
order by co2_mt desc
```

<DataTable data={owid_no_wb} rows=8>
    <Column id=country_name title="Country / territory"/>
    <Column id=region title="Region"/>
    <Column id=co2_mt title="CO₂ (Mt)" fmt="#,##0.0"/>
    <Column id=population title="Population" fmt="#,##0"/>
</DataTable>

<Details title="What is deliberately not here">

The dimension is authoritative for *what counts as a country*, so two things are
missing on purpose:

- **World Bank aggregates**: `WLD`, `EUU`, `OED` and the rest. WDI returns them
  in the same series as real countries; the dimension doesn't carry them, so the
  inner join to the spine keeps them out of every rollup on this site.
- **Antarctica.** OWID reports about 0.2 Mt of emissions for it. A null `region`
  should mean "not a country", and Antarctica is where that reading holds.
  Taiwan and ten small territories are the opposite case: they *are* countries
  the World Bank simply doesn't list, and `dbt/seeds/country_overrides.csv` puts
  them back.

</Details>

---

<small>Built from <code>marts.dim_country_year</code> and
<code>marts.fct_emissions_energy</code>. Sources:
<a href="https://github.com/owid/co2-data">OWID CO₂</a>,
<a href="https://github.com/owid/energy-data">OWID Energy</a>,
<a href="https://databank.worldbank.org/source/world-development-indicators">World Bank WDI</a>,
<a href="https://ec.europa.eu/eurostat/databrowser/view/nrg_pc_204">Eurostat</a>.</small>
