-- How many versions of each CO2 estimate the warehouse has held, from the
-- `snap_co2_estimates` SCD2 history: the first number we recorded, the one in
-- use now, and the difference between them.
-- Grain: one row per (country_iso3, year) in the snapshot.
--
-- `version_count = 1` — never revised while we were watching — is the normal
-- state, and the only state on a warehouse built from scratch. Filter on
-- `is_revised` for the restatements themselves.
with history as (
    select * from {{ ref('snap_co2_estimates') }}
),

country as (
    select * from {{ ref('dim_country') }}
),

versions as (
    select
        country_iso3,
        year,
        count(*) as version_count,
        min(dbt_valid_from) as first_loaded_at,
        -- null, not first_loaded_at, when there has only ever been one version
        case when count(*) > 1 then max(dbt_valid_from) end as last_revised_at,
        -- arg_min/arg_max: the value from the oldest and newest version rows
        arg_min(co2_mt, dbt_valid_from) as first_co2_mt,
        arg_max(co2_mt, dbt_valid_from) as latest_co2_mt,
        arg_min(co2_per_capita, dbt_valid_from) as first_co2_per_capita,
        arg_max(co2_per_capita, dbt_valid_from) as latest_co2_per_capita
    from history
    group by country_iso3, year
)

select
    v.country_iso3,
    d.country_name,
    d.region,
    v.year,
    v.version_count > 1 as is_revised,
    v.version_count,
    v.first_loaded_at,
    v.last_revised_at,
    v.first_co2_mt,
    v.latest_co2_mt,
    v.latest_co2_mt - v.first_co2_mt as co2_mt_change,
    case
        when v.first_co2_mt > 0
            then 100 * (v.latest_co2_mt - v.first_co2_mt) / v.first_co2_mt
    end as co2_mt_change_pct,
    v.first_co2_per_capita,
    v.latest_co2_per_capita
from versions as v
left join country as d on v.country_iso3 = d.country_iso3
