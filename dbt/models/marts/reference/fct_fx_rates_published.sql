{{
    config(
        materialized='incremental',
        unique_key=['rate_date', 'quote_currency'],
        incremental_strategy='delete+insert',
        on_schema_change='fail',
    )
}}

-- `on_schema_change='fail'` is not a preference: dbt refuses the default
-- (`ignore`) on a contracted incremental model, because the two say opposite
-- things — the contract promises a fixed shape, and `ignore` would let a column
-- quietly stop being written into the table that already exists. `fail` rather
-- than `append_new_columns` because a new column here means the *model* changed,
-- and this is the one table in the project that a rebuild has to rewrite 265k
-- rows to fix: stop, and let a person decide to run `--full-refresh`.
--
-- Every euro reference rate the ECB has actually published, as published.
-- Grain: one row per (rate_date, quote_currency). Sparse by design — no row on a
-- weekend, a TARGET holiday, or for a currency outside its quoted lifetime.
--
-- **This is the project's first incremental model**, and it is the right one
-- rather than the biggest one. Every other table here is a re-derivation of a
-- source that gets fully re-fetched: rebuilding them from scratch is not waste,
-- it is the only way to pick up a restatement. This table is the exception on
-- both counts — it grows by ~30 rows a day forever, and a fixing, once
-- published, does not change. A full rebuild is therefore O(all history) every
-- day to append one day, and that cost curve is the argument, not today's
-- absolute seconds (at 265k rows the rebuild is fast enough that the saving is
-- barely measurable — see CLAUDE.md for both numbers, honestly reported).
--
-- `delete+insert` rather than plain `append`, and the unique key rather than a
-- bare `>` on the date, because the *ingestion* re-asks for a lookback window:
-- an append would duplicate every row in it. This is the same idempotence
-- argument as `wb_wdi`'s merge key one layer up, and the reason the two windows
-- are configured separately is below.
with rates as (
    select s.* from {{ ref('stg_fx_rates') }} as s

    {% if is_incremental() %}
        -- Re-process a trailing window rather than only what is strictly newer,
        -- because the ingest layer re-asks for one too and a corrected fixing
        -- inside it would otherwise never reach this table.
        --
        -- `fx_incremental_lookback_days` must be >= `FX_LOOKBACK_DAYS` in
        -- `ingest/pipeline.py`. It is deliberately set larger instead of equal:
        -- two constants that have to match are a drift bug waiting to happen,
        -- whereas one that only has to be *no smaller* costs a few extra rows
        -- per run and cannot be wrong in the direction that loses data.
        where s.rate_date >= (
            select max(t.rate_date) - interval {{ var('fx_incremental_lookback_days') }} day
            from {{ this }} as t
        )
    {% endif %}
)

select
    rate_date,
    quote_currency,
    base_currency,
    units_per_eur,
    eur_per_unit,
    -- Computed rather than joined from `dim_date`. Keeping this model free of
    -- dependencies is what lets it be rebuilt or backfilled on its own, which is
    -- most of the point of it being incremental.
    cast(strftime(rate_date, '%Y%m%d') as integer) as date_key
from rates
