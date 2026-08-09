-- Energy fact, cleaned to (country iso, year) grain.
with source as (
    select * from {{ source('raw', 'owid_energy') }}
)

select
    iso_code as country_iso3,
    country,
    cast(year as integer) as year,
    -- primary energy: all end uses, but OWID only carries it for ~79 countries
    -- from 2024 on, so anything cut by the latest year thins out badly here
    primary_energy_consumption as primary_energy_twh,
    renewables_share_energy as renewables_share_pct,
    fossil_share_energy as fossil_share_pct,
    energy_per_capita,
    energy_per_gdp,
    -- electricity: a narrower slice of energy, but ~210 countries rather than
    -- 79, and `carbon_intensity_elec` is in gCO2 per kWh — a unit a reader can
    -- hold, unlike a share of a mix
    electricity_generation as electricity_generation_twh,
    carbon_intensity_elec as carbon_intensity_elec_g_kwh,
    low_carbon_share_elec as low_carbon_share_elec_pct,
    solar_share_elec as solar_share_elec_pct,
    wind_share_elec as wind_share_elec_pct,
    nuclear_share_elec as nuclear_share_elec_pct,
    coal_share_elec as coal_share_elec_pct,
    gas_share_elec as gas_share_elec_pct,
    -- Which extract these numbers came out of. dlt stamps `_dlt_load_id` at
    -- ingest as a unix epoch and `dbt source freshness` already reads it the
    -- same way; carrying it forward is what lets `dim_grid_emission_factors`
    -- say when the factor it publishes was pulled, which is the question an
    -- assurance provider asks about a disclosure input.
    to_timestamp(cast(_dlt_load_id as double)) as source_loaded_at
from source
where
    iso_code is not null
    and length(iso_code) = 3
