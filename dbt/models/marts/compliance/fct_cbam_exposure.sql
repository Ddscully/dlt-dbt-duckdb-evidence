-- What a tonne of an imported CBAM good costs at the EU border, by where it was
-- made.
--
-- From 2026 an importer of cement, fertiliser, aluminium, hydrogen or iron and
-- steel must surrender CBAM certificates for the emissions embedded in it. Where
-- they cannot get verified data from the installation that made it, they use the
-- country-specific default value from Annex I of Implementing Regulation (EU)
-- 2025/2621 — as corrected by (EU) 2026/1740, which replaced that annex in full
-- with retroactive effect from 1 January 2026 — with a mark-up. That annex is a
-- country x good carbon-intensity table; multiplied by a carbon price it is a
-- euro figure with a statutory deadline, and this model is that multiplication.
--
-- The mark-up is the part the correction moved. The annex used to publish each
-- good's marked-up value for 2026, 2027 and 2028 and this model read the
-- schedule off those columns; it now publishes only direct, indirect and total,
-- so the schedule comes from the `cbam_markup_schedule` seed instead. Two
-- columns went with the published ones — see `markup_2026_pct` below.
--
-- Grain: one row per (country or territory listed in the annex, good). 121
-- countries plus the fallback table, x the goods the annex actually prices.
-- Countries the annex does not list are deliberately absent — the regulation
-- sends every one of them to the same "other countries and territories" table,
-- so ranking them against each other would be ranking a hundred copies of one
-- number. That table is here as its own row instead, flagged.
--
-- **A screening tool, not a filing.** These are administrative values, and the
-- mark-up exists to make them worse than reality so that going and getting
-- verified supplier data pays. What this answers is which sourcing lanes are
-- worth that effort.
with resolved as (
    -- Annex I's numbers with its own fallback rule already applied. Extracted
    -- so the rule can be checked against the annex alone — see
    -- `int_cbam_default_factors`, which carries the reasoning that used to sit
    -- in four CTEs here.
    select * from {{ ref('int_cbam_default_factors') }}
),

goods as (
    select * from {{ ref('cbam_goods') }}
),

-- The phase-in mark-up, asserted from the regulation rather than measured off
-- the annex — which is a change, and not one this project chose.
--
-- Until the 2026/1740 correction the annex published each good's value
-- *including* the mark-up for 2026, 2027 and 2028 beside the plain total, so
-- this model divided one by the other and read the schedule off the data. That
-- was the better arrangement: an amendment moving a rate needed no edit here,
-- and it is how the fertiliser exception was found rather than assumed. The
-- correction publishes only direct, indirect and total, so there is nothing left
-- to divide and the schedule has to be stated.
--
-- Stated as a seed and not as a `case` or a var, so it is still reviewable as
-- data. The values are confirmed twice over: against the articles (10 / 20 / 30%
-- for cement, iron and steel, aluminium and hydrogen; a flat 1% for fertilisers,
-- a food-security carve-out) and against the February annex's own published
-- columns, where every one of the 10,929 priced rows implies exactly those rates
-- to within the third decimal the OJ prints.
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
    select * from {{ ref('stg_country') }}
),

-- The grid factor from `dim_grid_emission_factors`, purely as context. The
-- annex's *indirect* column is embedded electricity, so a country's grid is what
-- moves it — but the annex's own indirect figures come from IEA data under a
-- non-commercial licence this project deliberately does not redistribute, so
-- these are OWID's factors sitting beside the annex's numbers rather than
-- reconciled against them. Do not read the two as the same measurement.
grid as (
    select
        country_iso3,
        year as grid_factor_year,
        emission_factor_t_co2_per_mwh as grid_factor_t_co2_per_mwh
    from {{ ref('dim_grid_emission_factors') }}
    where is_latest_available
),

-- Certificates per tonne: the annex's total plus the group's statutory mark-up.
-- Every row is computed the same way now. Until the 2026/1740 correction the
-- annex published these three columns itself and this model preferred the
-- published figure, falling back to the computed one only where a cell was blank
-- — so `markup_is_inferred` marked the handful of rows where that happened. With
-- no published column left there is no distinction to draw, and the flag has
-- been dropped rather than shipped as a constant `true`.
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
)

select
    p.country_or_territory,
    p.country_iso3,
    c.country_name,
    -- What to put on a chart. `country_or_territory` is the annex's label and is
    -- kept because it is the legally meaningful one, but it arrives via Excel
    -- sheet names — which are capped at 31 characters, so North Korea is
    -- published here as "North Korea (Democratic People’" and South Korea as
    -- "Korea, Republic of (South Korea", both cut mid-parenthesis. (Before the
    -- 2026/1740 correction relabelled them the mangled pair were "Democratic
    -- Republic of the Cong" and "Myanmar_Burma" — the truncation moves with the
    -- names, which is the argument for coalescing rather than patching labels.)
    -- Falling back to the annex label covers the fallback table and any
    -- territory the country dimension does not carry.
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
    -- The emissions embedded in one tonne of the good, before the mark-up.
    p.direct_t_co2e_per_t,
    p.indirect_t_co2e_per_t,
    p.total_t_co2e_per_t,
    -- Certificates actually surrendered, i.e. after the mark-up, per tonne.
    p.certificates_2026_t_co2e_per_t,
    p.certificates_2027_t_co2e_per_t,
    p.certificates_2028_t_co2e_per_t,
    -- Kept although it is now derivable from the product group alone: it is the
    -- number a reader checks the mark-up with, and one column beats knowing the
    -- schedule. `markup_schedule_is_irregular` sat beside it until the 2026/1740
    -- correction, flagging the five Angola and Argentina cement rows that
    -- *compounded* the mark-up where the other 10,926 added it. That was a
    -- statement about the published columns, which no longer exist — with the
    -- schedule applied uniformly nothing can be irregular, so a column that is
    -- false on every row would be worse than no column.
    100 * (p.certificates_2026_t_co2e_per_t / nullif(p.total_t_co2e_per_t, 0) - 1) as markup_2026_pct,
    -- The euro figure. One reference price rather than a band, because the
    -- tonnage columns above are right there: the Evidence page multiplies them to
    -- draw the sensitivity across EUR 60-120 without rebuilding the model.
    {{ var('eu_ets_price_eur_per_t') }} as ets_price_eur_per_t,
    p.certificates_2026_t_co2e_per_t * {{ var('eu_ets_price_eur_per_t') }} as cbam_cost_2026_eur_per_t,
    p.certificates_2027_t_co2e_per_t * {{ var('eu_ets_price_eur_per_t') }} as cbam_cost_2027_eur_per_t,
    p.certificates_2028_t_co2e_per_t * {{ var('eu_ets_price_eur_per_t') }} as cbam_cost_2028_eur_per_t,
    -- How this country compares with the cheapest source of the same good, which
    -- is the procurement question. Over the annex's listed countries only.
    p.certificates_2026_t_co2e_per_t
    - min(p.certificates_2026_t_co2e_per_t) over (partition by p.good_key)
        as excess_over_cleanest_source_t_co2e_per_t,
    grid.grid_factor_year,
    grid.grid_factor_t_co2_per_mwh,
    -- Lineage, constant per row and deliberately so — this table ships as a
    -- standalone Parquet in the data release, and a euro figure detached from the
    -- instrument that sets it is worse than no figure.
    'Implementing Regulation (EU) 2025/2621, Annex I, '
    || 'as corrected by (EU) 2026/1740' as source_instrument,
    'location-based, administrative default' as factor_basis
from priced as p
left join countries as c on p.country_iso3 = c.country_iso3
left join grid on p.country_iso3 = grid.country_iso3
