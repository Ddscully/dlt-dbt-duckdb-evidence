-- What a tonne of an imported CBAM good costs at the EU border, by where it was
-- made. Grain and caveats are the description in _compliance.yml; the annex's
-- history and quirks are the `compliance-models` skill.
with resolved as (
    -- Annex I with its fallback rule applied — see `int_cbam_default_factors`.
    select * from {{ ref('int_cbam_default_factors') }}
),

goods as (
    select * from {{ ref('cbam_goods') }}
),

-- The phase-in mark-up per product group (see `markup_2026_pct` in the yml).
markup as (
    select
        product_group,
        max(markup_pct) filter (where year = 2026) / 100 as rate_2026,
        max(markup_pct) filter (where year = 2027) / 100 as rate_2027,
        max(markup_pct) filter (where year = 2028) / 100 as rate_2028
    from {{ ref('cbam_markup_schedule') }}
    group by product_group
),

countries as (
    select * from {{ ref('dim_country') }}
),

-- Context only: not the factor the annex used (see `grid_factor_t_co2_per_mwh`).
grid as (
    select
        country_iso3,
        year as grid_factor_year,
        emission_factor_t_co2_per_mwh as grid_factor_t_co2_per_mwh
    from {{ ref('dim_grid_emission_factors') }}
    where is_latest_available
),

-- Certificates per tonne: the annex's total plus the group's statutory mark-up.
priced as (
    select
        r.*,
        g.product_group,
        g.cn_code,
        g.goods_description,
        r.total_t_co2e_per_t * (1 + m.rate_2026) as certificates_2026_t_co2e_per_t,
        r.total_t_co2e_per_t * (1 + m.rate_2027) as certificates_2027_t_co2e_per_t,
        r.total_t_co2e_per_t * (1 + m.rate_2028) as certificates_2028_t_co2e_per_t
    from resolved as r
    inner join goods as g on r.good_key = g.good_key
    left join markup as m on g.product_group = m.product_group
),

-- The cheapest *listed* source of each good: the baseline for
-- `excess_over_cleanest_source_t_co2e_per_t`. The fallback table is excluded.
benchmarked as (
    select
        p.*,
        min(p.certificates_2026_t_co2e_per_t)
        filter (where not p.is_fallback_table)
            over (partition by p.good_key) as cheapest_listed_2026_t_co2e_per_t
    from priced as p
)

select
    p.country_or_territory,
    p.country_iso3,
    c.country_name,
    coalesce(c.country_name, p.country_or_territory) as country_display_name,
    c.region,
    c.income_group,
    p.product_group,
    p.cn_code,
    p.goods_description,
    p.good_key,
    p.production_route_code,
    p.is_fallback_table,
    p.is_country_specific,
    p.direct_t_co2e_per_t,
    p.indirect_t_co2e_per_t,
    p.total_t_co2e_per_t,
    p.certificates_2026_t_co2e_per_t,
    p.certificates_2027_t_co2e_per_t,
    p.certificates_2028_t_co2e_per_t,
    100 * (p.certificates_2026_t_co2e_per_t / nullif(p.total_t_co2e_per_t, 0) - 1) as markup_2026_pct,
    {{ var('eu_ets_price_eur_per_t') }} as ets_price_eur_per_t,
    p.certificates_2026_t_co2e_per_t * {{ var('eu_ets_price_eur_per_t') }} as cbam_cost_2026_eur_per_t,
    p.certificates_2027_t_co2e_per_t * {{ var('eu_ets_price_eur_per_t') }} as cbam_cost_2027_eur_per_t,
    p.certificates_2028_t_co2e_per_t * {{ var('eu_ets_price_eur_per_t') }} as cbam_cost_2028_eur_per_t,
    -- The fallback row neither sets the baseline nor gets a figure. Excluding it
    -- moves no number with today's annex, so a unit test holds the exclusion.
    case
        when not p.is_fallback_table
            then
                p.certificates_2026_t_co2e_per_t
                - p.cheapest_listed_2026_t_co2e_per_t
    end as excess_over_cleanest_source_t_co2e_per_t,
    grid.grid_factor_year,
    grid.grid_factor_t_co2_per_mwh,
    -- Lineage on every row: the table ships as a standalone Parquet file.
    'Implementing Regulation (EU) 2025/2621, Annex I, '
    || 'as corrected by (EU) 2026/1740' as source_instrument,
    'location-based, administrative default' as factor_basis
from benchmarked as p
left join countries as c on p.country_iso3 = c.country_iso3
left join grid on p.country_iso3 = grid.country_iso3
