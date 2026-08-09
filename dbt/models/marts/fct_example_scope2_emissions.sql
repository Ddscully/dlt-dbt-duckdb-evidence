-- The worked example: metered kWh x grid emission factor -> tonnes CO2e, which
-- is the whole of location-based Scope 2 accounting under the GHG Protocol.
--
-- **The twelve sites are invented** (`seeds/example_scope2_sites.csv`); the
-- factors they are multiplied by are not. This model is here because a reference
-- table nobody has shown you how to use is a column of numbers, and the arithmetic
-- is the difference between a climate dashboard and an input to a filing.
--
-- Grain: one row per site.
with sites as (
    select * from {{ ref('example_scope2_sites') }}
),

-- "The most recent published factor for this country", which is the row a
-- reporter wants and is a different year for different countries.
factors as (
    select
        country_iso3,
        country_name,
        year,
        emission_factor_g_co2_per_kwh,
        emission_factor_t_co2_per_mwh,
        low_carbon_share_elec_pct,
        latest_factor_lag_years
    from {{ ref('dim_grid_emission_factors') }}
    where is_latest_available
),

-- Left joined, not inner. A site in a country with no published factor is the
-- interesting failure — the group total quietly under-reports — so it has to
-- arrive as a row with a null factor that the `not_null` test catches, rather
-- than disappear.
priced as (
    select
        s.site_id,
        s.site_name,
        s.site_type,
        s.country_iso3,
        f.country_name,
        s.annual_electricity_mwh,
        f.year as factor_year,
        f.latest_factor_lag_years,
        f.emission_factor_g_co2_per_kwh,
        f.low_carbon_share_elec_pct,
        s.annual_electricity_mwh * f.emission_factor_t_co2_per_mwh as scope2_t_co2e
    from sites as s
    left join factors as f on s.country_iso3 = f.country_iso3
)

select
    p.site_id,
    p.site_name,
    p.site_type,
    p.country_iso3,
    p.country_name,
    p.annual_electricity_mwh,
    p.factor_year,
    p.latest_factor_lag_years,
    p.emission_factor_g_co2_per_kwh,
    p.low_carbon_share_elec_pct,
    p.scope2_t_co2e,
    100 * p.scope2_t_co2e / sum(p.scope2_t_co2e) over () as share_of_group_pct,
    -- The same consumption on the cleanest and dirtiest grid the group already
    -- operates on. Not a hypothetical extreme: every one of these is a country
    -- this company has a site in, so the spread is the cost of the siting
    -- decisions it has already made, and the two columns sum to a group total
    -- that can be read straight off a chart.
    p.annual_electricity_mwh * min(p.emission_factor_g_co2_per_kwh) over () / 1000
        as scope2_at_best_grid_t_co2e,
    p.annual_electricity_mwh * max(p.emission_factor_g_co2_per_kwh) over () / 1000
        as scope2_at_worst_grid_t_co2e
from priced as p
