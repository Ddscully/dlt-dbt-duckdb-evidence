{#
  SCD2 history of the grid emission factor, i.e. the number a company multiplies
  its metered kWh by to produce the location-based Scope 2 line in a CSRD, SECR
  or CDP disclosure.

  `snap_co2_estimates` keeps revisions because a restated inventory is
  interesting; this one keeps them because a *filed* number has to stay
  reconcilable. A reporter uses the factor published at the time, and if OWID
  restates it later the disclosure becomes one against a superseded factor,
  which the reporter has to be able to show.

  Grain: one row per (country_iso3, year, version). `dbt_valid_to is null` is
  the factor the warehouse is serving now.

  2015 onwards, not 1990: dbt cannot rebuild a snapshot, so it holds only what
  is worth keeping forever, and nobody files a disclosure against a 1994 factor.

  Nulls are filtered out: a country-year with no published factor is not a
  version of anything. `hard_deletes='invalidate'` then closes off a factor OWID
  withdraws with a `dbt_valid_to` instead of letting it vanish — "we had one and
  it was taken away" is a different fact from "there was never one".
#}
{% snapshot snap_grid_emission_factors %}

{{
    config(
        unique_key='country_year',
        group='compliance',
        strategy='check',
        check_cols=['carbon_intensity_elec_g_kwh'],
        hard_deletes='invalidate',
    )
}}

    select
        -- a snapshot needs one key column; the grain is still (country_iso3, year)
        country_iso3 || '-' || cast(year as varchar) as country_year,
        country_iso3,
        year,
        carbon_intensity_elec_g_kwh
    from {{ ref('stg_energy') }}
    where
        year >= 2015
        and carbon_intensity_elec_g_kwh is not null

{% endsnapshot %}
