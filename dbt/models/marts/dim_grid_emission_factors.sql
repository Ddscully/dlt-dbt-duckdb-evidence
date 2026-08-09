-- Grid emission factors, packaged as a reference table rather than a chart.
--
-- `carbon_intensity_elec_g_kwh` is already in `fct_emissions_energy`; this model
-- exists because the *use* is different. A reporter does not want a wide
-- country-year fact, they want "the current factor for country X, in the unit I
-- multiply my meter reading by, with its vintage and where it came from" — which
-- is a dimension, and which is why the vintage columns below carry as much
-- weight as the factor itself.
--
-- Grain: one row per (country_iso3, year) that has a published factor. Unlike
-- the fact, this is *not* on the full spine: a row with no factor is not a
-- reference value, it is an absence, and `dim_country_year` is where you go to
-- see absences as rows.
with factors as (
    select * from {{ ref('stg_energy') }}
),

spine as (
    select * from {{ ref('dim_country_year') }}
),

-- Point in time. Reporters file against the factor published *then*, so the
-- number a rebuild would give you today is not the whole answer — see
-- snapshots/snap_grid_emission_factors.sql. Everything here is null before 2015,
-- which is where the snapshot starts.
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
        -- The country's newest published factor, which is what "the current
        -- factor for country X" resolves to — and it is a different year for
        -- different countries, so a single `where year = 2025` would silently
        -- drop half the world. Same lesson as `latest_years.sql`, except here it
        -- is load-bearing for a number with a legal consequence.
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
    -- The factor, twice. g/kWh is how OWID publishes it and how anyone reading a
    -- chart holds it; t/MWh is the unit a reporter's meter data is in, and
    -- making them do the divide-by-1000 themselves is how a filing acquires a
    -- factor-of-1000 error.
    p.carbon_intensity_elec_g_kwh as emission_factor_g_co2_per_kwh,
    p.carbon_intensity_elec_g_kwh / 1000 as emission_factor_t_co2_per_mwh,
    -- Context for judging the factor rather than just using it: a 3 TWh grid
    -- moves on one new plant, and the low-carbon share says which direction.
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
    -- Lineage. Constant per row and deliberately so: this table is published as
    -- a standalone Parquet file in the data release, and a factor detached from
    -- its basis is the one thing a reporter must never be handed.
    'location-based' as factor_basis,
    'owid_energy.carbon_intensity_elec' as source_dataset,
    p.source_loaded_at
from published as p
inner join spine as s on p.country_iso3 = s.country_iso3 and p.year = s.year
left join versions as v on p.country_iso3 = v.country_iso3 and p.year = v.year
