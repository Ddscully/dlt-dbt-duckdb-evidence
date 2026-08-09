-- What a tonne of an imported CBAM good costs at the EU border, by where it was
-- made.
--
-- From 2026 an importer of cement, fertiliser, aluminium, hydrogen or iron and
-- steel must surrender CBAM certificates for the emissions embedded in it. Where
-- they cannot get verified data from the installation that made it, they use the
-- country-specific default value from Annex I of Implementing Regulation (EU)
-- 2025/2621, with a mark-up. That annex is a country x good carbon-intensity
-- table; multiplied by a carbon price it is a euro figure with a statutory
-- deadline, and this model is that multiplication.
--
-- Grain: one row per (country or territory listed in the annex, good). 119
-- countries plus the fallback table, x the 264 goods the annex actually prices.
-- Countries the annex does not list are deliberately absent — the regulation
-- sends every one of them to the same "other countries and territories" table,
-- so ranking them against each other would be ranking a hundred copies of one
-- number. That table is here as its own row instead, flagged.
--
-- **A screening tool, not a filing.** These are administrative values, and the
-- mark-up exists to make them worse than reality so that going and getting
-- verified supplier data pays. What this answers is which sourcing lanes are
-- worth that effort.
with defaults as (
    select * from {{ ref('cbam_default_values') }}
),

goods as (
    select * from {{ ref('cbam_goods') }}
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

-- 23 of the annex's 287 rows carry a description and no default value in any
-- country, not even the fallback: they are 4-digit CN *headings* (7211, 7318,
-- 3102, ...) printed above the 6- and 8-digit subheadings that hold the numbers,
-- and every one of them has such subheadings in the annex. They stay in the seed,
-- which is a transcription, and come out here, which is a table of euro costs —
-- 875 rows that could only ever have been priced at null.
priced_goods as (
    select good_key
    from defaults
    group by good_key
    having count(default_total_t_co2e_per_t) > 0
),

-- The mark-up each product group actually carries, taken from the annex rather
-- than from the articles that set it. Fertilisers come out at 1% in all three
-- years where every other group is 10 / 20 / 30%, which is 2,416 rows and
-- consistent across every country — so hardcoding one schedule would overstate
-- every fertiliser line by nine points in 2026 and twenty-seven in 2028.
-- Reading it off the data also means an amendment that changes a rate needs no
-- edit here.
group_markup as (
    select
        g.product_group,
        mode(round(d.default_2026_t_co2e_per_t / d.default_total_t_co2e_per_t, 4)) as rate_2026,
        mode(round(d.default_2027_t_co2e_per_t / d.default_total_t_co2e_per_t, 4)) as rate_2027,
        mode(round(d.default_2028_t_co2e_per_t / d.default_total_t_co2e_per_t, 4)) as rate_2028
    from defaults as d
    inner join goods as g on d.good_key = g.good_key
    where d.default_total_t_co2e_per_t > 0 and d.default_2026_t_co2e_per_t is not null
    group by g.product_group
),

-- The annex's catch-all, which is also its fallback rule: "where a country or
-- territory is not explicitly listed, the default value for the respective good
-- from the table 'Other countries and territories' needs to be selected", and
-- likewise "where a country or territory is explicitly listed but no value is
-- provided or the relevant field shows '-'".
--
-- That second half is load-bearing: one row in eight is a listed country with no
-- value for a good, so a model that only handled unlisted countries would leave
-- that many rows unpriced.
fallback as (
    select
        good_key,
        default_direct_t_co2e_per_t as fallback_direct,
        default_indirect_t_co2e_per_t as fallback_indirect,
        default_total_t_co2e_per_t as fallback_total,
        default_2026_t_co2e_per_t as fallback_2026,
        default_2027_t_co2e_per_t as fallback_2027,
        default_2028_t_co2e_per_t as fallback_2028
    from defaults
    where country_iso3 is null
),

-- **The fallback is a row-level rule, not a column-level one.** Picking each
-- column from whichever source happens to have it produces a row that exists
-- nowhere in the regulation: Chile's line pipe (7306 11 00) is published with a
-- total of 2,950 and a blank 2026 cell, and a per-column coalesce paired Chile's
-- tonnage with the *fallback's* mark-up for a 100% implied rate. So the source is
-- chosen once, on whether the country has a total, and every column comes from
-- it.
resolved as (
    select
        d.country_or_territory,
        d.country_iso3,
        d.good_key,
        d.production_route_code,
        d.country_iso3 is null as is_fallback_table,
        -- Which of the two the number came from. A reporter needs to know: a
        -- country-specific value estimates that country's average, and the
        -- fallback estimates the worst-covered part of the world.
        d.default_total_t_co2e_per_t is not null as is_country_specific,
        case
            when d.default_total_t_co2e_per_t is not null
                then d.default_direct_t_co2e_per_t
            else f.fallback_direct
        end as direct_t_co2e_per_t,
        case
            when d.default_total_t_co2e_per_t is not null
                then d.default_indirect_t_co2e_per_t
            else f.fallback_indirect
        end as indirect_t_co2e_per_t,
        coalesce(d.default_total_t_co2e_per_t, f.fallback_total) as total_t_co2e_per_t,
        case
            when d.default_total_t_co2e_per_t is not null
                then d.default_2026_t_co2e_per_t
            else f.fallback_2026
        end as published_2026,
        case
            when d.default_total_t_co2e_per_t is not null
                then d.default_2027_t_co2e_per_t
            else f.fallback_2027
        end as published_2027,
        case
            when d.default_total_t_co2e_per_t is not null
                then d.default_2028_t_co2e_per_t
            else f.fallback_2028
        end as published_2028
    from defaults as d
    inner join priced_goods as p on d.good_key = p.good_key
    left join fallback as f on d.good_key = f.good_key
),

-- Where the annex prints a total but leaves a mark-up cell blank — Chile's line
-- pipe is the only one today — the cell is filled from the product group's own
-- rate and flagged. The alternative is a null in a money column for a row whose
-- tonnage the regulation states plainly, which is the less honest of the two.
priced as (
    select
        r.*,
        g.product_group,
        g.cn_code,
        g.goods_description,
        r.published_2026 is null and r.total_t_co2e_per_t is not null as markup_is_inferred,
        coalesce(r.published_2026, r.total_t_co2e_per_t * m.rate_2026) as certificates_2026_t_co2e_per_t,
        coalesce(r.published_2027, r.total_t_co2e_per_t * m.rate_2027) as certificates_2027_t_co2e_per_t,
        coalesce(r.published_2028, r.total_t_co2e_per_t * m.rate_2028) as certificates_2028_t_co2e_per_t,
        m.rate_2028 as group_rate_2028
    from resolved as r
    inner join goods as g on r.good_key = g.good_key
    left join group_markup as m on g.product_group = m.product_group
)

select
    p.country_or_territory,
    p.country_iso3,
    c.country_name,
    -- What to put on a chart. `country_or_territory` is the annex's label and is
    -- kept because it is the legally meaningful one, but it arrives via Excel
    -- sheet names — which are capped at 31 characters and forbid some
    -- punctuation, so the Democratic Republic of the Congo is published here as
    -- "Democratic Republic of the Cong" and Myanmar as "Myanmar_Burma". Falling
    -- back to the annex label covers the fallback table and any territory the
    -- country dimension does not carry.
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
    p.markup_is_inferred,
    -- The emissions embedded in one tonne of the good, before the mark-up.
    p.direct_t_co2e_per_t,
    p.indirect_t_co2e_per_t,
    p.total_t_co2e_per_t,
    -- Certificates actually surrendered, i.e. after the mark-up, per tonne.
    p.certificates_2026_t_co2e_per_t,
    p.certificates_2027_t_co2e_per_t,
    p.certificates_2028_t_co2e_per_t,
    100 * (p.certificates_2026_t_co2e_per_t / nullif(p.total_t_co2e_per_t, 0) - 1) as markup_2026_pct,
    -- Five cement rows (Angola and Argentina) compound the mark-up rather than
    -- adding it — x1.1, x1.21, x1.331 — where every other row adds it. Compared
    -- against the group's *own* 2028 rate rather than one extrapolated from its
    -- 2026 rate: fertilisers are 1% in all three years, so an extrapolation
    -- (1 + 3 x 0.01) flags all 2,457 of them as irregular when none of them are.
    abs(
        p.certificates_2028_t_co2e_per_t / nullif(p.total_t_co2e_per_t, 0)
        - p.group_rate_2028
    ) > 0.005 as markup_schedule_is_irregular,
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
    'Implementing Regulation (EU) 2025/2621, Annex I' as source_instrument,
    'location-based, administrative default' as factor_basis
from priced as p
left join countries as c on p.country_iso3 = c.country_iso3
left join grid on p.country_iso3 = grid.country_iso3
