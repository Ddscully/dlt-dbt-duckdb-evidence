-- FX rates aggregated to the periods the rest of the warehouse thinks in.
-- Grain: one row per (period_type, period_start_date, currency_code), where
-- period_type is month / quarter / half / year.
--
-- **This model exists for one decision: spot or average.** Convert a *stock* (a
-- balance, a position at an instant) at the period-end rate and a *flow*
-- (revenue, spend over the period) at the period average. The wrong one still
-- yields a plausible number, so both ship, named for what they are, and
-- `period_end_vs_avg_pct` measures the gap — for EUR/USD, +11.7% in 2003.
--
-- * The average is over published fixings (`fct_fx_rates_published`), not
--   calendar days: the dense daily table repeats Friday's rate over the weekend,
--   weighting the average toward the days next to closures.
-- * `avg_eur_per_unit` is not 1 / `avg_units_per_eur` — the mean of reciprocals
--   is not the reciprocal of the mean. Each is the mean of its own series; the
--   period-end columns invert exactly.
-- * `period_is_complete` is false for the period in progress, whose average
--   covers only the days so far.
-- * For the rate on a given date use `fct_fx_rates_daily`, which gap-fills.
--
-- `period_end_is_stale` flags a period-end fixing older than the daily model's
-- carry-forward cap. It is computed from `last_rate_date` rather than joined
-- from `fct_fx_rates_daily`, because the daily model has no rows once a currency
-- leaves the ECB panel and a join would miss most stale period-ends. It
-- discloses rather than nulls: the last real fixing is what a period close at
-- that date would have used. The measured cases (the rouble's 2022 year end,
-- 305 days stale) are in the `currency-and-calendar` skill.
with published as (
    select * from {{ ref('fct_fx_rates_published') }}
),

calendar as (
    select * from {{ ref('dim_date') }}
),

series as (
    select max(rate_date) as series_end_date
    from published
),

dated as (
    select
        p.currency_code,
        p.base_currency,
        p.rate_date,
        p.units_per_eur,
        p.eur_per_unit,
        d.year,
        d.quarter,
        d.half,
        d.month_start_date,
        d.quarter_start_date,
        d.half_start_date,
        d.year_start_date
    from published as p
    inner join calendar as d on p.rate_date = d.date_day
),

period_types as (
    select unnest(['month', 'quarter', 'half', 'year']) as period_type
),

keyed as (
    select
        t.period_type,
        case t.period_type
            when 'month' then d.month_start_date
            when 'quarter' then d.quarter_start_date
            when 'half' then d.half_start_date
            else d.year_start_date
        end as period_start_date,
        case t.period_type
            when 'month' then strftime(d.month_start_date, '%Y-%m')
            when 'quarter' then d.year || '-q' || d.quarter
            when 'half' then d.year || '-' || d.half
            else cast(d.year as varchar)
        end as period_label,
        d.currency_code,
        d.base_currency,
        d.rate_date,
        d.units_per_eur,
        d.eur_per_unit
    from dated as d
    cross join period_types as t
),

aggregated as (
    select
        period_type,
        period_start_date,
        period_label,
        currency_code,
        min(base_currency) as base_currency,
        count(*) as n_published_days,
        min(rate_date) as first_rate_date,
        max(rate_date) as last_rate_date,
        avg(units_per_eur) as avg_units_per_eur,
        avg(eur_per_unit) as avg_eur_per_unit,
        arg_min(units_per_eur, rate_date) as period_start_units_per_eur,
        arg_max(units_per_eur, rate_date) as period_end_units_per_eur,
        arg_max(eur_per_unit, rate_date) as period_end_eur_per_unit,
        min(units_per_eur) as min_units_per_eur,
        max(units_per_eur) as max_units_per_eur
    from keyed
    group by period_type, period_start_date, period_label, currency_code
),

-- The period end, and the series end: an unfinished period's staleness is
-- measured to the series end, or every open period would be stale by
-- construction.
bounded as (
    select
        a.*,
        case a.period_type
            when 'month' then last_day(a.period_start_date)
            when 'quarter' then (a.period_start_date + interval 3 month) - interval 1 day
            when 'half' then (a.period_start_date + interval 6 month) - interval 1 day
            else (a.period_start_date + interval 1 year) - interval 1 day
        end as period_end_date,
        s.series_end_date
    from aggregated as a
    cross join series as s
)

select
    a.period_type,
    a.period_start_date,
    a.period_label,
    a.period_end_date,
    cast(date_part('year', a.period_start_date) as integer) as year,
    a.currency_code,
    a.base_currency,

    -- For flows: revenue, spend, a price over the period.
    a.avg_units_per_eur,
    a.avg_eur_per_unit,

    -- For stocks: a balance or position as at the period end.
    a.period_end_units_per_eur,
    a.period_end_eur_per_unit,
    a.period_start_units_per_eur,

    -- How far apart the two answers are, as a percentage of the average.
    100.0 * (a.period_end_units_per_eur - a.avg_units_per_eur)
    / nullif(a.avg_units_per_eur, 0) as period_end_vs_avg_pct,

    a.min_units_per_eur,
    a.max_units_per_eur,
    100.0 * (a.max_units_per_eur - a.min_units_per_eur)
    / nullif(a.min_units_per_eur, 0) as intra_period_range_pct,

    a.n_published_days,
    a.first_rate_date,
    a.last_rate_date,
    a.period_end_date <= a.series_end_date as period_is_complete,

    -- Age of the fixing behind `period_end_*` at the period (or series) end, and
    -- whether it exceeds the carry `fct_fx_rates_daily` allows.
    date_diff('day', a.last_rate_date, least(a.period_end_date, a.series_end_date))
        as period_end_stale_days,
    -- DuckDB resolves a select-list alias laterally, so the age is written once.
    period_end_stale_days > {{ var('fx_max_carry_forward_days') }} as period_end_is_stale
from bounded as a
