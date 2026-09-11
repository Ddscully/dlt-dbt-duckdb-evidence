-- ECB daily euro foreign-exchange reference rates, from Frankfurter.
-- Grain: one row per (rate_date, currency_code).
--
-- The landing table is already long (the resource unpivots the API's payload),
-- so this is a rename, a cast and the reciprocal. Gaps are not filled — weekends,
-- holidays and unquoted currencies are absent; `marts.fct_fx_rates_daily`
-- decides what to do about them.
--
-- **Both directions ship**, because an inverted rate still yields a plausible
-- number: `units_per_eur` is the ECB's quote (1 EUR buys 1.15 USD),
-- `eur_per_unit` its reciprocal.
with source as (
    select * from {{ source('raw', 'ecb_fx_rates') }}
)

select
    cast(rate_date as date) as rate_date,
    base_currency,
    -- Renamed to the conformed key `dim_currency` publishes. `raw` keeps the
    -- source's `quote_currency`, the natural name beside `base_currency`.
    quote_currency as currency_code,
    -- Units of the quote currency per 1 EUR — the ECB's own convention.
    rate as units_per_eur,
    -- EUR per 1 unit of the quote currency.
    1.0 / nullif(rate, 0) as eur_per_unit
from source
-- No `where rate is not null`: a null rate is a source defect for
-- `_staging.yml`'s test to report, not a row to drop quietly.
