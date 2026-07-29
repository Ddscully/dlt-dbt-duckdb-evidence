{#
  SCD2 history of OWID's CO2 estimates. OWID revises published years as
  countries restate their inventories, and every other model in this project
  overwrites the old number without noticing. This keeps the versions.

  Grain: one row per (country_iso3, year, version). `dbt_valid_to is null` is
  the number the warehouse is using now; anything else is a superseded estimate.

  Narrow on purpose — two columns from 1990 on, not the whole 60-column fact.
  A snapshot is the one table dbt can't rebuild, so it should hold only what is
  worth keeping forever.
#}
{% snapshot snap_co2_estimates %}

{{
    config(
        unique_key='country_year',
        strategy='check',
        check_cols=['co2_mt', 'co2_per_capita'],
        hard_deletes='invalidate',
    )
}}

    select
        -- a snapshot needs one key column; the grain is still (country_iso3, year)
        country_iso3 || '-' || cast(year as varchar) as country_year,
        country_iso3,
        year,
        co2_mt,
        co2_per_capita
    from {{ ref('stg_co2') }}
    where year >= 1990

{% endsnapshot %}
