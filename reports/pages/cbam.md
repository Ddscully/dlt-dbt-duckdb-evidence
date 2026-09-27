---
title: CBAM Exposure
description: What a tonne of an imported CBAM good costs at the EU border, by where it was made. Annex I's default values, priced at a carbon price you choose.
sidebar_position: 1
---

From 2026 an EU importer of steel, cement, aluminium, fertiliser or hydrogen pays
for the carbon embedded in it. Without data from the plant that made the goods,
the bill falls back to a country default published in
[Implementing Regulation (EU) 2025/2621](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=OJ%3AL_202502621),
as corrected in August 2026. This page prices those defaults.

```sql headline
select
    count(distinct good_key)                                as n_goods,
    -- The fallback table is a rule, not a place, so it is not a sourcing country.
    count(distinct country_or_territory)
        filter (where not is_fallback_table)                 as n_countries,
    max(ets_price_eur_per_t)                                as ets_price
from warehouse.cbam_exposure
```

```sql fallback_penalty
-- How much worse the "other countries and territories" fallback is than the
-- median country that *is* listed. This is the mark-up's whole design: being
-- unlisted, or being listed with no value for your good, should cost you.
with fb as (
    select good_key, certificates_2026_t_co2e_per_t as fallback_t
    from warehouse.cbam_exposure
    where is_fallback_table
),
listed as (
    select good_key, median(certificates_2026_t_co2e_per_t) as median_t
    from warehouse.cbam_exposure
    where is_country_specific and not is_fallback_table
    group by 1
)
select
    count(*)                                                    as n_goods,
    median(fb.fallback_t / nullif(listed.median_t, 0))           as median_ratio,
    count(*) filter (where fb.fallback_t > listed.median_t)      as n_worse
from fb inner join listed on fb.good_key = listed.good_key
```

<Grid cols=4>
    <BigValue data={headline} value=n_goods title="Goods priced"/>
    <BigValue data={headline} value=n_countries title="Sourcing countries"/>
    <BigValue data={headline} value=ets_price fmt='€#,##0' title="Carbon price assumed"/>
    <BigValue data={fallback_penalty} value=median_ratio fmt='0.00"×"' title="Unlisted vs. median country"/>
</Grid>

## The same tonne costs very different amounts at the border

```sql goods_list
select
    good_key                                                as value,
    product_group || ' · ' || cn_code || ' — ' || goods_description as label
from warehouse.cbam_exposure
where is_country_specific and not is_fallback_table
group by 1, 2
having count(*) >= 25
-- `order by product_group` would be a binder error: the select list is the two
-- grouped columns, so the only orderable thing here is the label, which starts
-- with the product group anyway.
order by label
```

<Dropdown data={goods_list} name=good value=value label=label defaultValue="72071190-semi-finished-products-of-iron-or-non-al" title="Good"/>

```sql ranked
-- One bar per sourcing country, coloured by what the annex says about how the
-- good is made. The route letters are the annex's own: E is scrap into an
-- electric arc furnace, C and F are iron ore through a blast furnace. A row the
-- annex left blank carries the catch-all value, route included, so it is
-- coloured as that and not as a route the country never earned.
select
    country_display_name,
    case
        when is_fallback_table then 'Catch-all row'
        when not is_country_specific then 'No country value (catch-all copied)'
        when production_route_code = 'E' then 'Route E: scrap, electric furnace'
        when production_route_code in ('C', 'F', 'C/F') then 'Route C/F: ore, blast furnace'
        when production_route_code is null then 'No route in the annex'
        else 'Route ' || production_route_code
    end                                                     as route,
    cbam_cost_2026_eur_per_t
from warehouse.cbam_exposure
where good_key = '${inputs.good.value}'
order by cbam_cost_2026_eur_per_t
```

```sql ranked_span
-- The catch-all row is not a sourcing country, so it cannot be the cheapest or
-- dearest source of anything; the mart keeps it out of its own window for the
-- same reason.
select
    count(*)                                                    as n,
    min(cbam_cost_2026_eur_per_t)                               as cheapest,
    max(cbam_cost_2026_eur_per_t)                               as dearest,
    -- A gap in euros, not a ratio: the ratio divides by the cheapest source, which
    -- is often one scrap-route country near zero and for one good is zero.
    max(cbam_cost_2026_eur_per_t) - min(cbam_cost_2026_eur_per_t) as gap,
    arg_min(country_display_name, cbam_cost_2026_eur_per_t)      as cheapest_country,
    arg_max(country_display_name, cbam_cost_2026_eur_per_t)      as dearest_country
from warehouse.cbam_exposure
where good_key = '${inputs.good.value}'
  and not is_fallback_table
```

<BarChart
    data={ranked}
    x=country_display_name
    y=cbam_cost_2026_eur_per_t
    series=route
    seriesColors={{
        'Route E: scrap, electric furnace': ['#1baf7a', '#199e70'],
        'Route C/F: ore, blast furnace': ['#b5530a', '#c7641b'],
        'No route in the annex': ['#2a78d6', '#3987e5'],
        'No country value (catch-all copied)': ['#b8bcc4', '#c8ccd3'],
        'Catch-all row': ['#5c6068', '#6c7078']
    }}
    sort=false
    yFmt='€#,##0'
    echartsOptions={{xAxis: {axisLabel: {show: false}}}}
    title="Border cost per tonne in 2026, one bar per sourcing country"
    subtitle="Cheapest to dearest; hover a bar for the country"
/>

For this good the 2026 cost runs from <Value data={ranked_span} column=cheapest_country/> at <Value data={ranked_span} column=cheapest fmt='€#,##0'/> a tonne to <Value data={ranked_span} column=dearest_country/> at <Value data={ranked_span} column=dearest fmt='€#,##0'/> a tonne, a gap of <Value data={ranked_span} column=gap fmt='€#,##0'/> on an identical product. For steel the colours sort almost perfectly: the production route, not the country, sets the price. **[Every country, with its route and value →](/cbam/by-country)**

## Electricity is almost none of it

```sql electricity_split
-- Averages over every country-specific value, excluding the catch-all table.
select
    product_group,
    'Burned in the process (direct)' as source,
    avg(direct_t_co2e_per_t) as t_co2e
from warehouse.cbam_exposure
where not is_fallback_table and total_t_co2e_per_t > 0
group by product_group
union all
select
    product_group,
    'Electricity drawn (indirect)',
    avg(coalesce(indirect_t_co2e_per_t, 0))
from warehouse.cbam_exposure
where not is_fallback_table and total_t_co2e_per_t > 0
group by product_group
```

<BarChart
    data={electricity_split}
    x=product_group
    y=t_co2e
    series=source
    seriesColors={{
        'Burned in the process (direct)': ['#b5530a', '#c7641b'],
        'Electricity drawn (indirect)': ['#2a78d6', '#3987e5']
    }}
    type=stacked100
    swapXY=true
    labels=true
    labelFmt="0%"
    chartAreaHeight=200
    title="What the priced carbon is, by product group"
/>

The annex counts electricity only for cement and fertilisers, and even there it
is under a tenth. So a country's grid barely moves its border cost: primary
aluminium costs about the same whether it was smelted on the cleanest grid or the
dirtiest. **[Why the grid barely counts →](/cbam/grid)**

## The euro figure is a price you choose

```sql sensitivity
with bounds as (
    select
        min(certificates_2026_t_co2e_per_t) as cheapest_t,
        max(certificates_2026_t_co2e_per_t) as dearest_t
    from warehouse.cbam_exposure
    where good_key = '${inputs.good.value}'
      and not is_fallback_table
),
prices as (select unnest([60, 75, 90, 105, 120]) as eur_per_t_co2)
select prices.eur_per_t_co2, 'Dearest source' as source, bounds.dearest_t * prices.eur_per_t_co2 as eur_per_t
from prices cross join bounds
union all
select prices.eur_per_t_co2, 'Cheapest source', bounds.cheapest_t * prices.eur_per_t_co2
from prices cross join bounds
order by 1
```

<LineChart
    data={sensitivity}
    x=eur_per_t_co2
    y=eur_per_t
    series=source
    seriesColors={{
        'Dearest source': ['#b5530a', '#c7641b'],
        'Cheapest source': ['#1baf7a', '#199e70']
    }}
    markers=true
    xFmt='€#,##0'
    yFmt='€#,##0'
    yMin=0
    echartsOptions={{xAxis: {min: 'dataMin', max: 'dataMax'}}}
    xAxisTitle="Carbon price per tonne of CO₂"
    title="Cost per tonne of the selected good, at €60 to €120 of carbon"
/>

There is no free public feed for the EU carbon price, so the page does not
pretend to quote one. The tonnage is fixed by law; the euros scale with whatever
price you assume, and the gap between suppliers scales with it.

## The bill rises every year to 2028

```sql markups
-- Years as text: three numeric ticks on a value axis come out as 2,026.5.
select product_group, '2026' as import_year, median(cbam_cost_2026_eur_per_t) as median_eur, 1 as ord from warehouse.cbam_exposure group by 1
union all
select product_group, '2027', median(cbam_cost_2027_eur_per_t), 2 from warehouse.cbam_exposure group by 1
union all
select product_group, '2028', median(cbam_cost_2028_eur_per_t), 3 from warehouse.cbam_exposure group by 1
order by ord
```

<LineChart
    data={markups}
    x=import_year
    y=median_eur
    series=product_group
    sort=false
    markers=true
    yFmt='€#,##0'
    title="Median border cost per tonne, by year of import"
/>

The defaults carry a mark-up of 10% in 2026, 20% in 2027 and 30% from 2028, so
the cost of *not* collecting supplier data grows each year. Fertilisers are the
exception, at a flat 1%. **[Method and limits →](/cbam/method)**

<Alert status=warning>

**A screening tool, not a filing.** These are administrative defaults, marked up
on purpose so that verified supplier data is the cheaper route. They rank which
sourcing lanes are worth collecting that data for.

</Alert>

<small>Source: <a href="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=OJ%3AL_202502621">Implementing Regulation (EU) 2025/2621</a>, Annex I, as corrected by <a href="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32026R1740">Implementing Regulation (EU) 2026/1740</a>. Modelled as <code>marts.fct_cbam_exposure</code>.</small>
