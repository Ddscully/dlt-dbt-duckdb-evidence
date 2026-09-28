---
title: By country
description: Every sourcing country's CBAM default for one good, with its production route, the basis of its value and its gross cost in 2026 and 2028.
sidebar_position: 1
---

[← CBAM Exposure](/cbam)

**For any good, what the regulation's default values put on a tonne, before the
deductions, depending only on where it was made, and which of those values are really the catch-all table
wearing a country's name.**

```sql goods_list
select
    good_key                                                as value,
    product_group || ' · ' || cn_code || ' — ' || goods_description as label
from warehouse.cbam_exposure
where is_country_specific and not is_fallback_table
group by 1, 2
having count(*) >= 25
order by label
```

<Dropdown data={goods_list} name=good value=value label=label defaultValue="72071190-semi-finished-products-of-iron-or-non-al" title="Good"/>

```sql ranked
select
    country_display_name,
    region,
    production_route_code,
    -- Where the annex prints "-" for a listed country the resolution rule copies
    -- the fallback row onto it *whole* (tonnage, certificates, cost and the
    -- production route with them), so 221 of the 11,037 rows this dropdown can
    -- reach, across 40 of its 252 goods, are the catch-all value wearing a
    -- country's name. 36 of them show a route letter the country never earned.
    -- Nothing in the numbers distinguishes those rows from a country-specific
    -- one, which is why this column is on the table rather than only in the query.
    --
    -- **`is_fallback_table` is tested first, and the order is the whole
    -- correctness of the column.** `is_country_specific` means "the annex
    -- printed a value in *this* row", not "this row is a country", and the annex
    -- does print one for "Other countries and territories", so all 260 fallback
    -- rows satisfy it. Asking `is_country_specific` first labelled the one row
    -- that is definitionally not a country `Country-specific`.
    case
        when is_fallback_table then 'Annex fallback (catch-all row)'
        when is_country_specific then 'Country-specific'
        else 'Annex fallback'
    end                                                     as value_basis,
    total_t_co2e_per_t,
    certificates_2026_t_co2e_per_t,
    cbam_cost_2026_eur_per_t,
    cbam_cost_2028_eur_per_t
from warehouse.cbam_exposure
where good_key = '${inputs.good.value}'
order by cbam_cost_2026_eur_per_t
```

<DataTable data={ranked} rows=15 search=true>
    <Column id=country_display_name title="Sourcing country"/>
    <Column id=production_route_code title="Route"/>
    <Column id=value_basis title="Value basis" align=left/>
    <Column id=total_t_co2e_per_t title="tCO₂e/t, before mark-up" fmt='0.000'/>
    <Column id=certificates_2026_t_co2e_per_t title="Certificates 2026" fmt='0.000'/>
    <Column id=cbam_cost_2026_eur_per_t title="€/t 2026" fmt='€#,##0.00' contentType=bar/>
    <Column id=cbam_cost_2028_eur_per_t title="€/t 2028" fmt='€#,##0.00'/>
</DataTable>

<Alert status=info>

**So what.** For steel the spread is not mainly about the national grid. It is
about the **production route**. The `Route` column is the annex's own indicator:
`E` is scrap into an electric arc furnace, `C` and `F` are ore through a blast
furnace. Sorting by cost sorts by route almost perfectly, and the countries at
the clean end are not the ones with clean grids. A procurement team screening
suppliers on country-level carbon data alone will pick the wrong lanes.

**Who acts:** procurement and whoever builds the supplier-screening model.
**Cost of getting it wrong:** ranking a scrap-route supplier and a blast-furnace
one as equivalent because they share a country.

</Alert>

## Read the value basis before the route

Where the annex prints "-" for a listed country, the regulation sends that whole
line to the "other countries and territories" table: tonnage, certificates, cost
*and* the production route together. Those rows say `Annex fallback`, and the
route letter on them belongs to the catch-all, not to the country. 221 of the
11,037 rows this dropdown can reach fall back, and 36 of them display a route the
country never earned. The catch-all row itself is in the table too, one per good,
labelled `Annex fallback (catch-all row)`.

Pick *Cement · 2523 90 00 90 — Other hydraulic cements* to see the shape of it:
24 of that good's 100 sourcing countries carry one identical tonnage between them,
against 39 distinct values across the other 76. They are still the values an importer
without supplier data must use; they are not evidence about how that country makes the good.

```sql fallback_penalty
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

For <Value data={fallback_penalty} column=n_worse/> of the <Value data={fallback_penalty} column=n_goods/> goods, the fallback value is worse than the median listed country; at the median it is <Value data={fallback_penalty} column=median_ratio fmt='0.00'/> times that country's value. That is the mechanism working as designed, since the defaults exist to make collecting real supplier data pay for itself.
