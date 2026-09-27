---
title: The archive
description: The warming trend in the weather archive, heating degree days by year, and the latest year by country.
sidebar_position: 3
---

[← Weather](/weather)

**The same series that fails to explain prices establishes something about itself: the capitals are warming, and heating demand is falling with them.**

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

The R² is <Value data={trend} column=fit fmt='0.00'/> on that pooled average, which is a real trend with a lot of weather noise on top of it — about what annual observations over this span can support, and no more.

{:else}

The archive here is <Value data={trend} column=n_years/> complete years deep, which is enough to compare one year against another and not enough to fit a
trend through. A published release carries the archive forward instead of
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

As of <Value data={partial} column=last_day/> the current year holds <Value data={partial} column=n_days/> days and <Value data={partial} column=hdd_total fmt='#,##0'/> heating degree days, which is <Value data={partial_vs_full} column=share_of_last_full_year fmt='0.0"%"'/> of last complete year's total. Charted beside the complete years it would read as a collapse in heating demand, and every chart in the weather section filters it out on a flag rather than trimming a year off by hand.

{:else}

Every year in the archive is a complete calendar year, so nothing here needs that
filter today. The models apply it anyway, because the current year becomes
partial the moment the archive is refreshed.

{/if}
