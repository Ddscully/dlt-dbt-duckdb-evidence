{{
    config(
        materialized='incremental',
        unique_key=['rate_date', 'currency_code'],
        incremental_strategy='delete+insert',
        on_schema_change='fail',
    )
}}

-- Every euro reference rate the ECB has published, as published.
-- Grain: one row per (rate_date, currency_code). Sparse by design — no row on a
-- weekend, a TARGET holiday, or outside a currency's quoted lifetime.
--
-- **The project's only incremental model**, because it is the only table that
-- grows by appending facts that never change: a published fixing is not
-- restated, so a full rebuild would reprocess all history to add one day. Every
-- other model is rebuilt, which is how restatements reach it.
--
-- `on_schema_change='fail'`: dbt refuses `ignore` on a contracted incremental
-- model, and a new column means the model changed — a person should decide on
-- `--full-refresh`. `delete+insert` on the unique key, not an append, because
-- the ingest re-asks for a lookback window whose rows would otherwise duplicate.
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
