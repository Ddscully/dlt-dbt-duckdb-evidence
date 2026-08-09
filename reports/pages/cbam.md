---
title: CBAM Exposure
description: What a tonne of an imported CBAM good costs at the EU border, by where it was made. Annex I's default values, priced at a carbon price you choose.
sidebar_position: 1
---

From 1 January 2026 an importer bringing cement, fertiliser, aluminium, hydrogen
or iron and steel into the EU must surrender **CBAM certificates** for the carbon
embedded in it. The first annual declaration, covering 2026 imports, is due in
2027.

Where the importer cannot get verified emissions data from the installation that
actually made the goods, they fall back to a **country-specific default value**
published in Annex I of [Implementing Regulation (EU) 2025/2621](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=OJ%3AL_202502621),
plus a mark-up. That annex is a country × good carbon-intensity table. Multiplied
by a carbon price it becomes a euro figure with a statutory deadline, and that
multiplication is the whole of this page.

```sql headline
select
    count(distinct good_key)                                as n_goods,
    -- The fallback table is a rule, not a place, so it is not a sourcing country.
    count(distinct country_or_territory)
        filter (where not is_fallback_table)                 as n_countries,
    max(ets_price_eur_per_t)                                as ets_price,
    count(*)                                                as n_rows
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
    <BigValue data={fallback_penalty} value=median_ratio fmt='0.00"×"' title="Fallback vs. median country"/>
</Grid>

For <Value data={fallback_penalty} column=n_worse/> of the <Value data={fallback_penalty} column=n_goods/> goods, the fallback value is worse than the median listed country, by a median factor of <Value data={fallback_penalty} column=median_ratio fmt='0.00'/>. That is the mechanism working as designed, since the defaults exist to make collecting real supplier data pay for itself.

## The same tonne, a different border cost

Pick a good. The ranking below is what the regulation says an importer owes per
tonne of it, depending only on which country it was made in.

```sql goods_list
select
    good_key                                                as value,
    product_group || ' · ' || cn_code || ' — ' || goods_description as label
from warehouse.cbam_exposure
where is_country_specific and not is_fallback_table
group by 1, 2
having count(*) >= 25
-- `order by product_group` would be a binder error: the select list is the two
-- grouped columns, so the only orderable thing here is the label — which starts
-- with the product group anyway.
order by label
```

<Dropdown data={goods_list} name=good value=value label=label defaultValue="72071190-semi-finished-products-of-iron-or-non-al" title="Good"/>

```sql ranked
select
    country_display_name,
    region,
    production_route_code,
    is_country_specific,
    total_t_co2e_per_t,
    certificates_2026_t_co2e_per_t,
    cbam_cost_2026_eur_per_t,
    cbam_cost_2028_eur_per_t
from warehouse.cbam_exposure
where good_key = '${inputs.good.value}'
order by cbam_cost_2026_eur_per_t
```

```sql ranked_span
-- The fallback table stays on the chart below, where it is a useful reference
-- line, but it is not a sourcing country and must not be counted as one or
-- become the "cheapest source" of anything.
select
    count(*)                                                    as n,
    min(cbam_cost_2026_eur_per_t)                               as cheapest,
    max(cbam_cost_2026_eur_per_t)                               as dearest,
    max(cbam_cost_2026_eur_per_t) / nullif(min(cbam_cost_2026_eur_per_t), 0) as spread,
    arg_min(country_display_name, cbam_cost_2026_eur_per_t)      as cheapest_country,
    arg_max(country_display_name, cbam_cost_2026_eur_per_t)      as dearest_country
from warehouse.cbam_exposure
where good_key = '${inputs.good.value}'
  and not is_fallback_table
```

Across <Value data={ranked_span} column=n/> sourcing countries the 2026 cost runs from <Value data={ranked_span} column=cheapest_country/> at <Value data={ranked_span} column=cheapest fmt='€#,##0.00'/> per tonne up to <Value data={ranked_span} column=dearest_country/> at <Value data={ranked_span} column=dearest fmt='€#,##0.00'/>, a spread of <Value data={ranked_span} column=spread fmt='0.0"×"'/> on an identical tonne of product.

<BarChart
    data={ranked}
    x=country_display_name
    y=cbam_cost_2026_eur_per_t
    swapXY=true
    title="CBAM cost per tonne, 2026"
    yFmt='€#,##0'
/>

<DataTable data={ranked} rows=12 search=true>
    <Column id=country_display_name title="Sourcing country"/>
    <Column id=production_route_code title="Route"/>
    <Column id=total_t_co2e_per_t title="tCO₂e/t, before mark-up" fmt='0.000'/>
    <Column id=certificates_2026_t_co2e_per_t title="Certificates 2026" fmt='0.000'/>
    <Column id=cbam_cost_2026_eur_per_t title="€/t 2026" fmt='€#,##0.00'/>
    <Column id=cbam_cost_2028_eur_per_t title="€/t 2028" fmt='€#,##0.00'/>
</DataTable>

<Alert status=info>

**So what.** For steel the spread is not mainly about the national grid. It is
about the **production route**. The `Route` column is the annex's own indicator:
`E` is scrap into an electric arc furnace, `C` and `F` are ore through a blast
furnace. Sorting by cost sorts by route almost perfectly, and the countries at
the clean end are not the ones with clean grids. This is the opposite of the
[Scope 2](/scope2) story, where the grid was the whole answer, and it is why a
procurement team screening suppliers on country-level carbon data alone will pick
the wrong lanes.

</Alert>

## Why the national grid barely enters the number

The section above says the grid is not the story. This one says why, and the
reason is not statistical — it is that the regulation mostly does not count
electricity at all.

```sql electricity_share
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
where not is_fallback_table
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
where not is_fallback_table
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

Indirect emissions — the carbon in the electricity the plant drew — are published
only for **cement and fertilisers**. For aluminium and hydrogen the annex carries
no indirect column at all, and for iron and steel it is present on 32 of 6,466
rows. Even where it does count, it is small: 7.5% of a cement tonne and 5.5% of a
fertiliser one. Across the whole annex, electricity is under **1%** of the carbon
being priced.

<Alert status=info>

**So what.** A country's grid can be among the dirtiest in the world and barely
move its CBAM bill, because the bill is overwhelmingly the carbon burned *in the
process* — the coke in a blast furnace, the calcination of limestone — not the
carbon behind the meter. That makes the weak correlation between a country's grid
factor and its border cost a fact about the regulation rather than a quirk of
this dataset, and it means the two carbon numbers this site publishes answer
different questions and are not substitutes. A grid factor is the right input to
a Scope 2 disclosure and the wrong input to a sourcing decision on steel.

**Who acts:** whoever is building a supplier-screening or carbon-cost model.
**Cost of getting it wrong:** ranking suppliers on grid data that the border cost
is almost entirely insensitive to.

</Alert>

## The price is a parameter, not a forecast

There is no clean free public API for EU ETS spot, so this page does not pretend
to quote one. The tonnage is fixed by the regulation; the euro figure is that
tonnage times a price you choose. Below is the same selected good at the cheapest
and dearest source, across the range EUAs have actually traded in.

```sql sensitivity
with bounds as (
    select
        arg_min(country_display_name, cbam_cost_2026_eur_per_t) as cheapest_country,
        arg_max(country_display_name, cbam_cost_2026_eur_per_t) as dearest_country,
        min(certificates_2026_t_co2e_per_t)                     as cheapest_t,
        max(certificates_2026_t_co2e_per_t)                     as dearest_t
    from warehouse.cbam_exposure
    where good_key = '${inputs.good.value}'
      and not is_fallback_table
),
prices as (select unnest([60, 75, 90, 105, 120]) as eur_per_t_co2)
select
    prices.eur_per_t_co2,
    bounds.cheapest_country,
    bounds.cheapest_t * prices.eur_per_t_co2                    as cheapest_eur_per_t,
    bounds.dearest_country,
    bounds.dearest_t * prices.eur_per_t_co2                     as dearest_eur_per_t,
    (bounds.dearest_t - bounds.cheapest_t) * prices.eur_per_t_co2 as gap_eur_per_t
from prices cross join bounds
order by prices.eur_per_t_co2
```

<DataTable data={sensitivity}>
    <Column id=eur_per_t_co2 title="Carbon price €/tCO₂e" fmt='€#,##0'/>
    <Column id=cheapest_country title="Cheapest source"/>
    <Column id=cheapest_eur_per_t title="€/t of good" fmt='€#,##0.00'/>
    <Column id=dearest_country title="Dearest source"/>
    <Column id=dearest_eur_per_t title="€/t of good" fmt='€#,##0.00'/>
    <Column id=gap_eur_per_t title="Gap" fmt='€#,##0.00'/>
</DataTable>

## The mark-up escalates, and not uniformly

The defaults are applied with a mark-up that rises over the phase-in: **10% in
2026, 20% in 2027, 30% from 2028**. Fertilisers are the exception at 1%, in all
three years.

```sql markups
select
    product_group,
    count(*)                                            as n_rows,
    avg(markup_2026_pct)                                as markup_pct,
    median(cbam_cost_2026_eur_per_t)                    as median_2026,
    median(cbam_cost_2028_eur_per_t)                    as median_2028
from warehouse.cbam_exposure
group by 1
order by median_2026 desc
```

<DataTable data={markups}>
    <Column id=product_group title="Product group"/>
    <Column id=n_rows title="Rows" fmt='#,##0'/>
    <Column id=markup_pct title="2026 mark-up" fmt='0.0"%"'/>
    <Column id=median_2026 title="Median €/t, 2026" fmt='€#,##0.00'/>
    <Column id=median_2028 title="Median €/t, 2028" fmt='€#,##0.00'/>
</DataTable>

The mark-up is read off the published values rather than asserted from the
articles, and the difference matters: hardcoding 10/20/30% would overstate every
one of the 2,457 fertiliser rows by nine points in 2026 and twenty-seven by
2028.

## What this is not

<Alert status=warning>

**A screening tool, not a filing.** Every number here is an *administrative
default*: an estimate of a country's average, deliberately marked up so that
obtaining verified installation data is the cheaper path. A real importer with
supplier data will use that instead and will usually pay less. What this ranks is
which sourcing lanes are worth the effort of going to get that data.

</Alert>

Three more limits worth stating plainly, because a practitioner will check them
first:

- **A CN code alone does not identify a row.** 2523 10 00 is both white clinker
  and grey clinker, with default values differing by more than a factor of two.
  Classification to the right description is the importer's problem and the annex
  does not solve it.
- **The annex has defects, and they are reproduced rather than corrected.**
  Albania's white Portland cement is published with its three values shifted into
  the mark-up columns; five cement rows for Angola and Argentina compound the
  mark-up instead of adding it; Chile's line pipe is missing its 2026 cell
  entirely. The seed transcribes the regulation as it stands and the mart flags
  each case, since a legal instrument is not this project's to tidy up.
- **The grid factor shown elsewhere in this warehouse is not the annex's.** The
  regulation's own electricity emission factors come from IEA data under a
  non-commercial licence, which this project deliberately does not redistribute.
  Where the annex publishes a direct/indirect split at all, which covers cement
  and fertilisers and almost none of iron and steel, the indirect part is
  electricity, but it cannot be reconciled against the OWID-derived factors on
  the [Scope 2](/scope2) page. They sit beside each other; they are not the same
  measurement.

The underlying table is `marts.fct_cbam_exposure`, and the transcribed annex is
the `cbam_default_values` and `cbam_goods` seeds. The mart ships in the
[data release](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/releases/latest)
both as Parquet and inside the DuckDB file; the two seeds are in the DuckDB
file's `main` schema only.
