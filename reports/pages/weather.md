---
title: Weather
description: Whether it was a colder year is the first explanation to rule out before crediting an energy number to policy, price or efficiency. Across 499 country-years of EU capitals it explains 0.0% of what electricity prices did.
sidebar_position: 5
---

Every other page here answers *what happened*. This one exists to answer *was it
just colder*, which is the question you have to dispose of before an energy or
price movement can be credited to anything else.

It is the only source in this warehouse with a finite budget — Open-Meteo meters
requests rather than rows — so the scope is deliberately narrow: daily ERA5
reanalysis for the capital city of each EU/EEA country, aggregated to the year
and turned into degree days.

```sql panel
select
    count(distinct country_iso3) as n_countries,
    count(distinct year) filter (where year_is_complete) as n_complete_years,
    min(year) filter (where year_is_complete) as first_year,
    max(year) filter (where year_is_complete) as last_year,
    max(grid_distance_km) as furthest_grid_km
from warehouse.country_weather_year
```

```sql pooled
select
    count(*) as n_observations,
    count(distinct year) as n_year_pairs,
    corr(hdd_change, price_change) as pooled_correlation,
    100 * regr_r2(price_change, hdd_change) as variance_explained
from warehouse.weather_price_pairs
```

<Grid cols=4>
    <BigValue data={panel} value=n_countries title="Capitals in the archive"/>
    <BigValue data={panel} value=n_complete_years title="Complete calendar years"/>
    <BigValue data={pooled} value=n_observations fmt='#,##0' title="Country-years compared"/>
    <BigValue data={pooled} value=variance_explained fmt='0.0"%"' title="Of price movement explained by weather"/>
</Grid>

## Was it just colder that year?

```sql base
select
    max(heating_base_c) as heating_base_c,
    max(cooling_base_c) as cooling_base_c
from warehouse.country_weather_year
```

Heating degree days are the standard demand proxy: for each day, how far the mean temperature sat below a base of <Value data={base} column=heating_base_c fmt='0.0'/>°C, summed over the year. If weather drove the European electricity market, a country whose heating demand jumped ought to be a country whose price jumped.

```sql correlation_by_year
select
    cast(cast(year as integer) as varchar) as year_label,
    corr(hdd_change, price_change) as correlation,
    count(*) as n_countries
from warehouse.weather_price_pairs
group by 1
order by 1
```

```sql extremes
with by_year as (
    select corr(hdd_change, price_change) as correlation
    from warehouse.weather_price_pairs
    group by year
)
select
    max(abs(correlation)) as strongest,
    100 * max(correlation * correlation) as best_year_variance
from by_year
```

<BarChart
    data={correlation_by_year}
    x=year_label
    y=correlation
    sort=false
    yMin={-1}
    yMax={1}
    yFmt='0.00'
    title="Correlation between a country's change in heating demand and its change in electricity price"
    subtitle="One bar per year-over-year pair. The scale is the full range a correlation can take."
/>

A correlation runs from -1 to +1, which is why the axis above is drawn over the
whole range rather than zoomed to the bars. Across <Value data={pooled} column=n_year_pairs/> consecutive year-pairs the strongest relationship in any single year is <Value data={extremes} column=strongest fmt='0.00'/> in absolute terms, and even that year leaves only <Value data={extremes} column=best_year_variance fmt='0.0"%"'/> of the variation in price accounted for. Pooled over all <Value data={pooled} column=n_observations fmt='#,##0'/> country-years the correlation is <Value data={pooled} column=pooled_correlation fmt='0.000'/> and the share of price movement it explains rounds to <Value data={pooled} column=variance_explained fmt='0.0"%"'/> of the total.

That is not a weak effect. It is the absence of one, measured the same way
thirteen times.

```sql widest_year
select
    cast(cast(year as integer) as varchar) as year_label,
    max(price_spread) as price_spread,
    max(hdd_spread) as hdd_spread,
    count(*) as n_countries
from warehouse.weather_price_pairs
where is_widest_spread_year
group by year
```

```sql widest_scatter
select country_name, country_iso3, hdd_change, price_change
from warehouse.weather_price_pairs
where is_widest_spread_year
```

```sql widest_outlier
select country_name, hdd_change, price_change
from warehouse.weather_price_pairs
where is_widest_spread_year
order by price_change desc
limit 1
```

Prices diverged most in <Value data={widest_year} column=year_label/> across <Value data={widest_year} column=n_countries/> countries, where the price change spanned <Value data={widest_year} column=price_spread fmt='#,##0.0'/> percentage points while heating demand spanned <Value data={widest_year} column=hdd_spread fmt='#,##0.0'/> points.

<ScatterPlot
    data={widest_scatter}
    x=hdd_change
    y=price_change
    series=country_iso3
    legend=false
    xFmt='0"%"'
    yFmt='0"%"'
    xAxisTitle="Change in heating degree days"
    yAxisTitle="Change in household electricity price"
    title="One point per country, for the year prices diverged most"
/>

The point at the top is <Value data={widest_outlier} column=country_name/> at <Value data={widest_outlier} column=price_change fmt='#,##0.0"%"'/> for the year, and it is not a weather story at all: the Dutch energy-tax cut landed in the first half of 2022, so that year's annual average is a price nobody paid for a full year and the year after rebounds against it. Its heating demand moved <Value data={widest_outlier} column=hdd_change fmt='0.0"%"'/> over the same pair.

<Alert status=info>

**So what.** When somebody attributes an energy or emissions movement to policy,
efficiency or fuel switching, "it was a milder year" is the cheapest competing
explanation and usually the one nobody checks. This is the table that rules it in
or out. Here it rules it out: over the whole EU/EEA panel, the year-over-year
change in heating demand carries essentially no information about the
year-over-year change in household electricity price, in any year measured. What
is left is tax, network cost and gas exposure — which is where the
[Currency](/currency) page picks the story up, since a further slice of the same
movement turns out to be the euro rather than the electricity.

</Alert>

## What the archive does show

The same series that fails to explain prices does establish something about
itself.

```sql trend
with by_year as (
    select
        cast(year as integer) as year,
        avg(temp_mean_c) as mean_c,
        avg(hdd_total) as hdd
    from warehouse.country_weather_year
    where year_is_complete
    group by 1
)
select
    count(*) as n_years,
    10 * regr_slope(mean_c, year) as deg_c_per_decade,
    -- Negated: the slope is negative and the sentence that reads it says
    -- "fall by", so the column carries the direction and the number stays positive.
    -1 * regr_slope(hdd, year) as hdd_fall_per_year,
    regr_r2(mean_c, year) as fit
from by_year
```

```sql slopes
with per_country as (
    select
        country_iso3,
        regr_slope(temp_mean_c, cast(year as integer)) as slope
    from warehouse.country_weather_year
    where year_is_complete
    group by 1
    having count(*) >= 5
)
select
    count(*) as n_countries,
    count(*) filter (where slope > 0) as n_warming
from per_country
```

```sql hdd_by_year
select
    cast(cast(year as integer) as varchar) as year_label,
    avg(hdd_total) as hdd_total,
    avg(temp_mean_c) as mean_c
from warehouse.country_weather_year
where year_is_complete
group by 1
order by 1
```

{#if trend[0].n_years >= 8}

Fitted across <Value data={trend} column=n_years/> complete years, the mean temperature of these capitals rises <Value data={trend} column=deg_c_per_decade fmt='0.00'/>°C per decade, and annual heating degree days fall by <Value data={trend} column=hdd_fall_per_year fmt='0.0'/> a year. Fitted per country instead of on the pooled average, <Value data={slopes} column=n_warming/> of <Value data={slopes} column=n_countries/> capitals are warming.

The fit is <Value data={trend} column=fit fmt='0.00'/> on that pooled average, which is a real trend with a lot of weather noise on top of it — about what annual observations over this span can support, and no more.

{:else}

The archive here is <Value data={trend} column=n_years/> complete years deep, which is enough to compare one year against another and not enough to fit a
trend through. A published release carries the archive forward rather than
refetching it, so this section gets stronger every month rather than resetting.

{/if}

<BarChart
    data={hdd_by_year}
    x=year_label
    y=hdd_total
    sort=false
    yFmt='#,##0'
    title="Average heating degree days across the capitals, complete years only"
    subtitle="Bars rather than a line: the archive can have gaps, and a line would draw a confident segment across one."
/>

Bars rather than a line is not a style choice. The years here are whichever ones
have been fetched, a line chart interpolates across any that have not, and an
invented segment between two real observations is indistinguishable from data.

```sql latest_year_detail
select
    w.country_name,
    w.hdd_total,
    w.cdd_total,
    w.temp_mean_c,
    w.frost_days
from warehouse.country_weather_year w
where w.year_is_complete
    and w.year = (select max(year) from warehouse.country_weather_year where year_is_complete)
order by w.hdd_total desc
```

<DataTable data={latest_year_detail} rows=12 search=true>
    <Column id=country_name title="Country"/>
    <Column id=hdd_total title="Heating degree days" fmt='#,##0'/>
    <Column id=cdd_total title="Cooling degree days" fmt='#,##0'/>
    <Column id=temp_mean_c title="Mean temp, °C" fmt='0.0'/>
    <Column id=frost_days title="Frost days" fmt='0'/>
</DataTable>

## Two degree-day conventions, and they disagree

A degree day needs a daily temperature, and there are two answers to what that
is. `hdd_total` uses the day's mean; `hdd_minmax_total` uses the midpoint of the
day's maximum and minimum, which is what a station-based series reports because
it is all a max/min thermometer can record. Neither is more correct, and this
warehouse ships both rather than picking one silently.

```sql conventions
select
    count(*) as n_country_years,
    100.0 * avg(abs(hdd_minmax_total - hdd_total) / hdd_total) as average_gap,
    100.0 * max(abs(hdd_minmax_total - hdd_total) / hdd_total) as widest_gap,
    100.0 * max(abs(hdd_minmax_total - hdd_total) / hdd_total)
        filter (where hdd_total >= 1000) as widest_with_heating_season,
    count(*) filter (where hdd_total < 1000) as n_barely_heated
from warehouse.country_weather_year
where year_is_complete and hdd_total > 0
```

```sql convention_worst
select
    country_name,
    cast(cast(year as integer) as varchar) as year_label,
    hdd_total,
    hdd_minmax_total,
    100.0 * (hdd_minmax_total - hdd_total) / hdd_total as gap
from warehouse.country_weather_year
where year_is_complete and hdd_total > 0
order by abs(hdd_minmax_total - hdd_total) / hdd_total desc
limit 8
```

Over <Value data={conventions} column=n_country_years fmt='#,##0'/> complete country-years the two conventions differ by <Value data={conventions} column=average_gap fmt='0.0"%"'/> on average and by as much as <Value data={conventions} column=widest_gap fmt='0.0"%"'/> at the extreme.

The extreme is a small-denominator effect rather than a measurement problem, and
the table below shows it: the worst disagreements are all places with barely any
heating season, where a few degree days either way is a large share of a small
total. Restricted to country-years with a real winter — a thousand degree days or
more — the widest gap is <Value data={conventions} column=widest_with_heating_season fmt='0.0"%"'/> and <Value data={conventions} column=n_barely_heated/> of the country-years fall below that line.

<DataTable data={convention_worst}>
    <Column id=country_name title="Country"/>
    <Column id=year_label title="Year"/>
    <Column id=hdd_total title="Mean-based" fmt='#,##0'/>
    <Column id=hdd_minmax_total title="Min/max-based" fmt='#,##0'/>
    <Column id=gap title="Difference" fmt='0.0"%"'/>
</DataTable>

<Alert status=warning>

**A degree-day total lifted out of this warehouse is meaningless without its
base and its convention.** Both are carried on every row for that reason —
`heating_base_c` and the two separate totals — rather than being settled once in
a project config file that the number then travels away from. The same reasoning
puts `fiscal_year_start_month` on every row of `dim_date` rather than in a
project variable the number then travels away from.

</Alert>

## A capital is not a country

The archive holds one grid cell per country, at its capital city. That is a
coarse proxy for national heating demand and the model says so with a number
rather than a caveat.

```sql grid
select
    country_name,
    country_iso3,
    max(grid_distance_km) as grid_distance_km
from warehouse.country_weather_year
group by 1, 2
order by 3 desc
limit 8
```

```sql grid_summary
select
    avg(grid_distance_km) as average_km,
    max(grid_distance_km) as furthest_km
from (select distinct country_iso3, grid_distance_km from warehouse.country_weather_year)
```

ERA5 answers on a 0.25-degree grid, so a request snaps to the nearest cell centre
and the response reports where it actually landed. Averaged over the capitals
that displacement is <Value data={grid_summary} column=average_km fmt='0.0'/> km, and the furthest capital sits <Value data={grid_summary} column=furthest_km fmt='0.0'/> km from the cell that answered for it.

<DataTable data={grid}>
    <Column id=country_name title="Country"/>
    <Column id=grid_distance_km title="Capital to grid cell, km" fmt='0.0'/>
</DataTable>

That displacement is small and it is not the approximation that matters. The
approximation that matters has no number here at all: Madrid's climate is not
Spain's, and one cell stands in for a whole national population. Comparing a
country against *itself* across years — which is what every degree-day column on
this page is used for — survives that. Comparing two countries against each other
does not, and no column on this page should be read as a ranking of national
climate.

## The current year is not comparable

```sql partial
select
    cast(cast(year as integer) as varchar) as year_label,
    max(n_days) as n_days,
    max(last_day) as last_day,
    avg(hdd_total) as hdd_total
from warehouse.country_weather_year
where not year_is_complete
group by 1
```

```sql partial_vs_full
select
    100.0 * (
        (select avg(hdd_total) from warehouse.country_weather_year where not year_is_complete)
        / (
            select avg(hdd_total) from warehouse.country_weather_year
            where year_is_complete
                and year = (select max(year) from warehouse.country_weather_year where year_is_complete)
        )
    ) as share_of_last_full_year
```

{#if partial.length > 0}

The archive stops a few days short of today, so the current year is always
partial and an annual degree-day total over a partial year is not comparable with
a whole one.

As of <Value data={partial} column=last_day/> the current year holds <Value data={partial} column=n_days/> days and <Value data={partial} column=hdd_total fmt='#,##0'/> heating degree days, which is <Value data={partial_vs_full} column=share_of_last_full_year fmt='0.0"%"'/> of last complete year's total. Charted beside the complete years it would read as a collapse in heating demand, and every bar chart on this page filters it out through `year_is_complete` rather than trimming a year off by hand.

{:else}

Every year in the archive is a complete calendar year, so nothing here needs the
`year_is_complete` filter today. The models apply it anyway — the current year
becomes partial the moment the archive is refreshed.

{/if}

## Limitations

- **EU/EEA only.** The scope was chosen to match
  `marts.fct_eu_electricity_prices_semiannual` exactly, so the two join with no
  gaps. Joining this to `marts.fct_emissions_energy` leaves the rest of the world
  null, the same way the electricity price column already does.
- **One cell per country.** See above: fine for a country against itself, weak
  for one country against another. A population-weighted average over many cells
  is the honest version and costs many times the API budget this source has.
- **The recent tail is preliminary.** Open-Meteo serves ERA5T within a day or two
  of real time and Copernicus replaces it with final ERA5 two to three months
  later, so rows inside the last ninety days can change value between builds.
  Rows older than that are frozen, because they are carried forward between
  releases rather than refetched.
- **Degree days are a demand proxy, not demand.** They know nothing about
  building stock, insulation, occupancy or what a country heats with. A cold
  country with well-insulated housing and a mild one without can land the same
  way round on this page and the opposite way round on a gas bill.
- **A correlation across countries is not a causal test.** The finding above is
  that the simplest weather explanation does not fit, which is what a control
  variable is for. It is not evidence for any particular alternative.

The tables are `marts.fct_country_weather_year` (this page) and
`staging.stg_weather_daily` (the daily grain underneath it, one row per country
and date). Both ship in the
[data release](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/releases/latest),
along with the raw ERA5 landing table, which is the one table in this warehouse a
rebuild cannot reproduce inside the source's daily budget.

<small>Weather data by <a href="https://open-meteo.com/">Open-Meteo</a> (CC BY
4.0), derived from ERA5 reanalysis produced by
<a href="https://www.ecmwf.int/">ECMWF</a> for the
<a href="https://climate.copernicus.eu/">Copernicus Climate Change Service</a>.</small>
