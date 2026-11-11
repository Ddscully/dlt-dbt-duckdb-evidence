-- Period aggregates for every currency, all four period types (~20k rows). Left
-- unfiltered so the page's dropdown can move between currencies and between
-- monthly and annual grains without a source per combination — and because a
-- source query that comes back empty is written as a 0-byte parquet and fails
-- the build.
--
-- Columns are named, not `select *`: the result ships to every visitor's
-- browser, so it carries what the pages read and a new mart column does not
-- join it unasked. A page that needs another column adds it here first.
select
    period_type,
    period_start_date,
    period_label,
    year,
    currency_code,
    avg_units_per_eur,
    period_end_units_per_eur,
    period_end_vs_avg_pct,
    intra_period_range_pct,
    last_rate_date,
    period_is_complete,
    period_end_stale_days,
    period_end_is_stale
from marts.fct_fx_rates_periods
