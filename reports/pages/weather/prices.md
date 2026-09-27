---
title: Prices
description: Heating demand against household electricity prices across European capitals, year by year.
sidebar_position: 1
---

[← Weather](/weather)

**If weather drove the European electricity market, a country whose heating demand jumped would be a country whose price jumped. In no year measured is that so.**

```sql pooled
select
    count(*) as n_observations,
    count(distinct year) as n_year_pairs,
    corr(hdd_change, price_change) as pooled_correlation,
    100 * regr_r2(price_change, hdd_change) as variance_explained
from warehouse.weather_price_pairs
```

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

That is not a weak effect. It is the absence of one, measured the same way in
every year-pair above.

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

**So what.** For prices, weather is ruled out: over the whole European panel, the
year-over-year change in heating demand carries essentially no information about
the year-over-year change in household electricity price, in any year measured.
What is left is tax, network cost and gas exposure — which is where the
[Currency](/currency) page picks the story up, since a further slice of the same
movement turns out to be the euro rather than the electricity.

</Alert>
