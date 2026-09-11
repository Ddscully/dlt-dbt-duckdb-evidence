-- Annex I's default carbon intensities, with the regulation's fallback rule
-- already applied — the carbon half of `fct_cbam_exposure`, before any price,
-- mark-up or country decoration is put on it.
-- Grain: one row per (country or territory listed in the annex, good).
--
-- Annex I (Implementing Regulation (EU) 2025/2621, as corrected by 2026/1740)
-- is a country x good table of embedded emissions per tonne, and many cells are
-- absent; the regulation says what to use for each kind of absence. That rule is
-- this model, separate from the mart because it depends on the annex alone and
-- can be unit-tested against it alone.
with defaults as (
    select * from {{ ref('cbam_default_values') }}
),

-- 23 of the 283 goods have no value in any country, not even the fallback: they
-- are CN *headings* (7211, 7318, 3102, ...) whose values sit in the subheadings
-- below them. The seed transcribes them; this table of intensities drops them.
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
-- The second half matters: about one listed-country row in eight has no value.
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

-- **The fallback is a row-level rule, not a column-level one.** Taking each
-- column from whichever row has it builds a row the regulation never states, so
-- the source row is chosen once — on whether the country has a total — and every
-- column comes from it. (The `compliance-models` skill has the case that proved
-- it.)
select
    d.country_or_territory,
    d.country_iso3,
    d.good_key,
    -- From the same row as the tonnages: the route explains the value, so a
    -- fallen-back row takes the fallback's route.
    case
        when d.default_total_t_co2e_per_t is not null
            then d.production_route_code
        else f.fallback_route
    end as production_route_code,
    d.country_iso3 is null as is_fallback_table,
    -- Which source the number came from: a country's own estimate, or the
    -- fallback's.
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
