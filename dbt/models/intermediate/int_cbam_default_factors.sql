-- Annex I's default carbon intensities, with the regulation's fallback rule
-- already applied — the carbon half of `fct_cbam_exposure`, before any price,
-- mark-up or country decoration is put on it.
-- Grain: one row per (country or territory listed in the annex, good).
--
-- Implementing Regulation (EU) 2025/2621 Annex I, as corrected by (EU)
-- 2026/1740, is a country x good table of embedded emissions per tonne. Reading
-- a value out of it is not a lookup: a third of the cells are absent, and the
-- regulation says exactly what to do about each kind of absence. That rule is
-- what this model is, and it is separate from the mart because it is separately
-- true — it depends on the annex alone, so it can be checked against the annex
-- alone. The two unit tests on it used to reach it through the mart and had to
-- pose a markup schedule, a country dimension and an empty grid-factor table to
-- do it, none of which the rule touches.
with defaults as (
    select * from {{ ref('cbam_default_values') }}
),

-- 23 of the annex's 283 goods carry a description and no default value in any
-- country, not even the fallback: they are 4-digit CN *headings* (7211, 7318,
-- 3102, ...) printed above the longer subheadings that hold the numbers, and
-- every one of them has such subheadings in the annex. Two of them say so in
-- words, printing "see below" where a value would go. They stay in the seed,
-- which is a transcription, and come out here, which is a table of carbon
-- intensities — rows that could only ever have been priced at null.
priced_goods as (
    select good_key
    from defaults
    group by good_key
    having count(default_total_t_co2e_per_t) > 0
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
        production_route_code as fallback_route
    from defaults
    where country_iso3 is null
)

-- **The fallback is a row-level rule, not a column-level one.** Picking each
-- column from whichever source happens to have it produces a row that exists
-- nowhere in the regulation. The case that proved it is gone from the source —
-- Chile's line pipe (7306 11 00) was published with a total of 2,950 and a blank
-- 2026 mark-up cell, and a per-column coalesce paired Chile's tonnage with the
-- *fallback's* mark-up for a 100% implied rate — but the rule it established
-- outlives it, and direct/indirect/total are still three columns that must be
-- read off one row. So the source is chosen once, on whether the country has a
-- total, and every column comes from it.
select
    d.country_or_territory,
    d.country_iso3,
    d.good_key,
    -- Read off the same row as the tonnages, for the reason above: the
    -- route is a property of the *value*, not of the country. Annex I
    -- prints it in the row it explains — "(C) carbon steel via BF/BOF" is
    -- why that row says 8,21 and another says 0,13 — so a fallen-back row
    -- takes the fallback's route with the fallback's numbers.
    case
        when d.default_total_t_co2e_per_t is not null
            then d.production_route_code
        else f.fallback_route
    end as production_route_code,
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
    coalesce(d.default_total_t_co2e_per_t, f.fallback_total) as total_t_co2e_per_t
from defaults as d
inner join priced_goods as p on d.good_key = p.good_key
left join fallback as f on d.good_key = f.good_key
