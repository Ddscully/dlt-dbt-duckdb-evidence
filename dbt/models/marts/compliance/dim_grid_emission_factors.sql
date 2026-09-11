-- Grid emission factors, packaged as a reference table rather than a chart.
--
-- The factor is also in `fct_emissions_energy`; this serves a different use: "the
-- current factor for country X, in the unit I multiply a meter reading by, with
-- its vintage and source" — hence the vintage columns.
--
-- Grain: one row per (country_iso3, year) with a published factor — not the full
-- spine, since a missing factor is an absence, not a reference value.
with factors as (
    select * from {{ ref('stg_energy') }}
),

spine as (
    select * from {{ ref('dim_country_year') }}
),

-- Point in time: a report files against the factor published *then*, which the
-- snapshot (from 2015) records. Null before 2015.
versions as (
    select
        country_iso3,
        year,
        count(*) as factor_version_count,
        min(dbt_valid_from) as first_published_at,
        -- null, not first_published_at, when there has only ever been one version
        case when count(*) > 1 then max(dbt_valid_from) end as last_revised_at,
        arg_min(carbon_intensity_elec_g_kwh, dbt_valid_from) as first_published_factor
    from {{ ref('snap_grid_emission_factors') }}
    group by country_iso3, year
),

published as (
    select
        f.country_iso3,
        f.year,
        f.carbon_intensity_elec_g_kwh,
        f.electricity_generation_twh,
        f.low_carbon_share_elec_pct,
        f.source_loaded_at,
        -- The country's newest factor year, which differs by country — a single
        -- `where year = …` would silently drop much of the world.
        max(f.year) over (partition by f.country_iso3) as latest_available_year,
        -- The newest factor year anywhere in the table: the frontier a country's
        -- own latest year is measured against.
        max(f.year) over () as frontier_year
    from factors as f
    where f.carbon_intensity_elec_g_kwh is not null
)

select
    p.country_iso3,
    s.country_name,
    s.region,
    s.income_group,
    p.year,
    -- Twice: g/kWh as OWID publishes it, t/MWh as meter data is reported, so no
    -- one has to divide by 1000 by hand.
    p.carbon_intensity_elec_g_kwh as emission_factor_g_co2_per_kwh,
    p.carbon_intensity_elec_g_kwh / 1000 as emission_factor_t_co2_per_mwh,
    -- Context: a small grid moves on one new plant.
    p.electricity_generation_twh,
    p.low_carbon_share_elec_pct,
    -- Vintage
    p.year = p.latest_available_year as is_latest_available,
    p.latest_available_year,
    p.frontier_year - p.latest_available_year as latest_factor_lag_years,
    -- Revision history (null before the snapshot's 2015 floor)
    v.factor_version_count,
    v.first_published_factor as first_published_factor_g_co2_per_kwh,
    v.factor_version_count > 1 as is_restated,
    v.first_published_at,
    v.last_revised_at,
    -- Lineage on every row: the table ships as a standalone Parquet file.
    'location-based' as factor_basis,
    'owid_energy.carbon_intensity_elec' as source_dataset,
    p.source_loaded_at
from published as p
inner join spine as s on p.country_iso3 = s.country_iso3 and p.year = s.year
left join versions as v on p.country_iso3 = v.country_iso3 and p.year = v.year
