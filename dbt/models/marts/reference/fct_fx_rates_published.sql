{{
    config(
        materialized='incremental',
        unique_key=['rate_date', 'currency_code'],
        incremental_strategy='delete+insert',
        on_schema_change='fail',
    )
}}

-- Every euro reference rate the ECB has published, as published. Why it is the
-- only incremental model is the description in _reference.yml.
--
-- `on_schema_change='fail'`: dbt refuses `ignore` on a contracted incremental
-- model, and a new column means the model changed, so a person should decide on
-- `--full-refresh`.
with rates as (
    select s.* from {{ ref('stg_fx_rates') }} as s

    {% if is_incremental() %}
        -- A trailing window, since the ingest re-asks for one and a corrected
        -- fixing inside it must reach this table. `fx_incremental_lookback_days`
        -- need only be no smaller than `FX_LOOKBACK_DAYS` in
        -- `ingest/sources/ecb.py`, so it is set larger rather than kept equal.
        where s.rate_date >= (
            select max(t.rate_date) - interval {{ var('fx_incremental_lookback_days') }} day
            from {{ this }} as t
        )
    {% endif %}
)

select
    rate_date,
    currency_code,
    base_currency,
    units_per_eur,
    eur_per_unit,
    -- Computed rather than joined from `dim_date`, so the model depends only on
    -- its source and can be rebuilt or backfilled alone.
    cast(strftime(rate_date, '%Y%m%d') as integer) as date_key
from rates
