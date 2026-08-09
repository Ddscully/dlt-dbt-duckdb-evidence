{#
  SCD2 history of the grid emission factor, i.e. the number a company multiplies
  its metered kWh by to produce the location-based Scope 2 line in a CSRD, SECR
  or CDP disclosure.

  This is the second snapshot in the project and it exists for a different
  reason to the first. `snap_co2_estimates` keeps revisions because a restated
  national inventory is interesting. This one keeps them because a *filed*
  number has to stay reconcilable: a reporter uses the factor that was published
  at the time, and if OWID restates 2024 next spring, the disclosure does not
  retroactively become wrong — it becomes a disclosure against a superseded
  factor, which is a thing you have to be able to demonstrate.

  Grain: one row per (country_iso3, year, version). `dbt_valid_to is null` is
  the factor the warehouse is serving now.

  2015 onwards, not 1990. A snapshot is the one table dbt can't rebuild, so it
  should hold only what is worth keeping forever, and nobody files a disclosure
  against a 1994 factor. Ten years of vintages is already more than any
  reporting framework reaches back for.

  Nulls are filtered out rather than snapshotted: a country-year with no
  published factor is not a version of anything. `hard_deletes='invalidate'`
  then means a factor OWID withdraws is closed off with a `dbt_valid_to`
  instead of vanishing, which is the behaviour a reporter needs — "we had one
  and it was taken away" is a different fact from "there was never one".
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
