-- FX rates aggregated to the periods the rest of the warehouse thinks in.
-- Grain: one row per (period_type, period_start_date, currency_code), where
-- period_type is month / quarter / half / year.
--
-- **This model exists for one decision: spot or average.** Converting a *stock*
-- — a balance, an inventory, a debt, anything that is a position at an instant —
-- uses the closing rate on that instant. Converting a *flow* — revenue, spend, a
-- price paid over a period — uses the period average, because the flow happened
-- across the whole period and no single day's rate represents it. Using one
-- where the other belongs is a standard finance-data bug and it is invisible in
-- the output: a plausible number comes out either way. So both ship, named for
-- what they are, and nothing here picks for the caller.
--
-- The size of getting it wrong is not academic, and `period_end_vs_avg_pct` is
-- the column that measures it: for EUR/USD it reaches +11.7% in 2003, +10.9% in
-- 2002 and -8.6% in 2014, so a year of euro flows converted at the closing rate
-- is misstated by more than the margin on most of the businesses doing the
-- converting. Across every currency the worst complete year is the Icelandic
-- krona in 2008 at +98%, which is a collapse rather than a drift but converts
-- just as silently.
--
-- Two things that are easy to get wrong in the aggregation itself:
--
-- * **The average is over published fixings, not over calendar days.** Averaging
--   the dense `fct_fx_rates_daily` would count every Friday three times (Friday,
--   Saturday, Sunday all carry Friday's rate) and four or five times over a
--   holiday weekend — a silent weighting toward whichever weekday the closures
--   fall next to. This model reads `fct_fx_rates_published` for exactly that
--   reason.
-- * **`avg_eur_per_unit` is not 1 / `avg_units_per_eur`.** The mean of the
--   reciprocals is not the reciprocal of the mean, and the gap grows with how
--   much the rate moved inside the period: for EUR/USD it is 0.07% in the calm
--   2015 and 0.53% in 2008. Each column is the mean of its own series, which is
--   the honest construction; convert in the direction the rate is quoted where
--   you have the choice. The period-*end* columns have no such problem — a
--   single point inverts exactly.
--
-- `period_is_complete` is the `price_is_partial_year` lesson from the Eurostat
-- models, arriving again: the current year's average is over the months that
-- have happened, and a chart that puts it beside finished years without saying
-- so is comparing seven months with twelve.
--
-- **For a rate on a *given date*, use `fct_fx_rates_daily`** — it gap-fills the
-- 30% of calendar days the ECB does not publish on, which this model does not
-- attempt. Where the two meet they agree: of the 19,649 complete period-ends,
-- 6,035 (31%) fall on a day with no fixing at all and the carried value is the
-- same in both.
--
-- **`period_end_is_stale` is this model's half of the staleness policy, and it
-- has to be computed here rather than read off the sibling.** `period_end_*` is
-- `arg_max` over *published* fixings, so on its own it has no notion of
-- staleness and will quote a fixing of any age as a period close;
-- `fct_fx_rates_daily` caps its carry at `fx_max_carry_forward_days` and nulls
-- the rate past it. The obvious way to mirror that — join the sibling and read
-- `is_rate_stale` — **reproduces the defect it is meant to catch**, because the
-- daily model stops emitting rows entirely once a currency leaves the panel: 17
-- of the 22 stale period-ends have no daily row to join to, so a left join
-- flags 5 of 22 and calls the rest clean. The flag below asks the question
-- directly instead, of `last_rate_date` alone.
--
-- **22 complete period-ends across 7 currencies are stale, and they have three
-- causes rather than one.** Sixteen are currencies that simply left the ECB's
-- panel and whose retirement `dim_currency` deliberately does not guess at —
-- the rouble on 2022-03-01, and the peso, dinar, dirham and Taiwan dollar
-- together on 2020-10-30, one panel change rather than four events. Five are
-- the currency crises (the krona's 2008, which lands on month, quarter, half
-- and year alike; the peso's January 2002). One is a redenomination the seed
-- *does* record, ROL in 2005 — and it is still stale here, because this model
-- reads fixings and not the seed.
--
-- The worst is not the most famous. The krona's +98.3% in 2008 is quoted
-- everywhere in this project and is 22 days stale; **the rouble's 2022 year end
-- is 305 days stale**, a 117.201 close against an 88.397 average, because the
-- ECB stopped publishing RUB ten months before the year ended. Both numbers are
-- true statements about converting at the last available fixing, which is why
-- the flag discloses rather than nulls — see `period_end_is_stale`.
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

-- The period end, and the last date any value in this row could be speaking
-- for. Computed once here because three columns below need it, and because the
-- staleness clock has to stop at the end of the *series* for a period that has
-- not finished: measured against `period_end_date` alone, every unfinished
-- period is stale by construction, which is 87 of the 116 open periods and no
-- information at all.
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

    -- How far apart the two answers are for this period, as a percentage of the
    -- average. This is the column that turns "spot or average" from a style
    -- question into a number.
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

    -- How old the fixing behind `period_end_*` is, relative to the last date
    -- that value could be speaking for, and whether that exceeds the carry
    -- `fct_fx_rates_daily` allows. The flag discloses and does not null: 290.00
    -- ISK/EUR *was* the last real fixing of 2008 and a finance system closing an
    -- ISK balance on 31 December would have used it. What the reader needs is
    -- that it was three weeks old because the currency had stopped trading.
    date_diff('day', a.last_rate_date, least(a.period_end_date, a.series_end_date))
        as period_end_stale_days,
    -- DuckDB resolves a select-list alias laterally, so the age is written once.
    period_end_stale_days > {{ var('fx_max_carry_forward_days') }} as period_end_is_stale
from bounded as a
