-- Annex I's default carbon intensities with the regulation's fallback rule
-- applied: the carbon half of `fct_cbam_exposure`. The rule and the annex's
-- quirks are the description in _intermediate.yml.
with defaults as (
    select * from {{ ref('cbam_default_values') }}
),

-- Drops the CN headings with no value anywhere; their subheadings carry it.
priced_goods as (
    select good_key
    from defaults
    group by good_key
    having count(default_total_t_co2e_per_t) > 0
),

-- The "Other countries and territories" row: used where a country is unlisted,
-- or listed with no value.
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

-- Row-level, not column-level: every column comes from the row chosen on
-- whether the country has a total (the `compliance-models` skill has the case).
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
