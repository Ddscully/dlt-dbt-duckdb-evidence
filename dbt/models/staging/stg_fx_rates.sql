-- ECB daily euro foreign-exchange reference rates, from Frankfurter.
-- Grain: one row per (rate_date, currency_code).
--
-- The landing table is already long — the resource unpivots the API's wide
-- `{date: {currency: rate}}` payload so the merge key is a real key — so this
-- model is a rename, a cast and the reciprocal. What it deliberately does *not*
-- do is fill gaps: a weekend, a TARGET holiday or a currency the ECB has stopped
-- quoting is simply absent here, and `marts.fct_fx_rates_daily` is where that
-- becomes a decision.
--
-- **Both directions ship, and that is not redundancy.** Getting an FX rate the
-- wrong way up is the classic money bug, and it is invisible in the result: a
-- number comes out either way. `units_per_eur` is the source's own quote (one
-- euro buys 1.15 dollars); `eur_per_unit` is its reciprocal (one dollar buys
-- 0.87 euro). Same argument as shipping the Scope 2 factor in both g/kWh and
-- t/MWh — a consumer forced to invert it themselves is a consumer who will
-- eventually forget to.
with source as (
    select * from {{ source('raw', 'ecb_fx_rates') }}
)

select
    cast(rate_date as date) as rate_date,
    base_currency,
    -- `currency_code`, not `quote_currency`, and this alias is the whole of the
    -- rename. `dim_currency` publishes `currency_code`; two of the three FX
    -- facts used to spell the same dimension a second way, so nothing joined
    -- them and the bus matrix showed `fct_fx_rates_periods` conforming to
    -- nothing at all. The landing table keeps `quote_currency` deliberately —
    -- `raw` is what we were handed and renaming it means a dlt schema drop and
    -- a re-fetch, and "the quote side of a pair" is the right name for a column
    -- sitting next to `base_currency` anyway. Staging is where a source's words
    -- become the warehouse's.
    quote_currency as currency_code,
    -- Units of the quote currency per 1 EUR — the ECB's own convention.
    rate as units_per_eur,
    -- EUR per 1 unit of the quote currency.
    1.0 / nullif(rate, 0) as eur_per_unit
from source
-- No `where rate is not null` on purpose: a null rate is a source defect, and
-- filtering it here would turn it into a quietly shorter table. `_staging.yml`
-- tests it instead, so it fails loudly with the offending rows stored.
