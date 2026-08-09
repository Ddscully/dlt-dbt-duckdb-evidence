-- The euro reference rates on *every* calendar day, gap-filled.
-- Grain: one row per (date_day, currency_code), within each currency's quoted
-- lifetime.
--
-- The ECB publishes on TARGET settlement days, so 7,066 of the 10,078 calendar
-- days since 1999-01-04 carry a fixing and **the other 30% carry nothing** —
-- 2,878 weekend days and 134 weekday closures (Good Friday, Easter Monday,
-- 1 May, 25-26 December, and 1999-12-31 for the millennium changeover). A
-- consumer with a transaction dated on a Sunday has to do *something*, and every
-- option is a modelling decision rather than a lookup:
--
--   * **carry the last fixing forward** — what this model does, and what a
--     finance system does, because Sunday's contractual rate is Friday's;
--   * interpolate — plausible-looking and wrong: it invents a mid-market rate
--     nobody could have dealt at, and it needs the future to compute the past;
--   * leave it null — correct and useless, since it pushes the same decision
--     into every downstream query, differently each time.
--
-- Carrying forward is the same operation as a slowly-changing lookup ("what was
-- true as at this date"), and it has the same two failure modes, both handled
-- here rather than left to the caller:
--
-- 1. **Outside a currency's lifetime there is nothing to carry.** The spine is
--    built between `first_published_date` and `last_published_date` per
--    currency, so the Croatian kuna stops on 2022-12-30 instead of being carried
--    into the euro era, and no currency has rates before it was quoted.
-- 2. **A suspended quote is not a long weekend.** Within the lifetime, the carry
--    is capped at `fx_max_carry_forward_days` (7). The longest closure in the
--    whole series is 5 days, so every real weekend and holiday is filled, and
--    exactly two interior gaps are not — both of them currency crises rather
--    than calendars. The Icelandic krona has 3,333 stale days between the 2008
--    banking collapse and February 2018; the Argentine peso has 26, from the
--    January 2002 breaking of the dollar peg. Those rows exist, with
--    `is_rate_stale` set and a null rate — an absence you can count, rather than
--    nine years of a rate nobody could have dealt at.
--
-- Today that is 265,035 published rows, 113,020 carried forward (29.6%, which is
-- the weekend and holiday share of a calendar) and 3,359 stale.
--
-- `rate_source_date` says which fixing each row is actually quoting, which is
-- the column that makes the whole thing auditable.
with calendar as (
    select * from {{ ref('dim_date') }}
),

currency as (
    select * from {{ ref('dim_currency') }}
    where is_quoted
),

published as (
    select * from {{ ref('fct_fx_rates_published') }}
),

-- One row per currency per day it was quoted on. Not the full cross join: a
-- dense rectangle over all 46 currencies would be 40% rows that never existed.
spine as (
    select
        c.currency_code,
        d.date_day,
        d.date_key,
        d.year,
        d.quarter,
        d.month,
        d.half,
        d.is_weekday
    from currency as c
    inner join calendar as d
        on d.date_day between c.first_published_date and c.last_published_date
),

observed as (
    select
        s.currency_code,
        s.date_day,
        s.date_key,
        s.year,
        s.quarter,
        s.month,
        s.half,
        s.is_weekday,
        p.units_per_eur,
        p.eur_per_unit,
        p.rate_date
    from spine as s
    left join published as p
        on
            s.currency_code = p.quote_currency
            and s.date_day = p.rate_date
),

filled as (
    select
        *,
        last_value(units_per_eur ignore nulls) over carried as carried_units_per_eur,
        last_value(eur_per_unit ignore nulls) over carried as carried_eur_per_unit,
        last_value(rate_date ignore nulls) over carried as rate_source_date
    from observed
    -- Unbounded preceding to the current row: the last *known* value as at this
    -- day, never a later one. A default window frame would reach forward and
    -- backfill the past with the future.
    window carried as (
        partition by currency_code
        order by date_day
        rows between unbounded preceding and current row
    )
),

aged as (
    select
        *,
        date_diff('day', rate_source_date, date_day) as days_since_published_rate
    from filled
),

base as (
    select distinct base_currency from published
)

select
    f.date_day,
    f.date_key,
    f.currency_code,
    b.base_currency,

    -- The usable rate: null once the carry has run past the cap, so a stale
    -- number can't be multiplied by anything by accident.
    case
        when f.days_since_published_rate <= {{ var('fx_max_carry_forward_days') }}
            then f.carried_units_per_eur
    end as units_per_eur,
    case
        when f.days_since_published_rate <= {{ var('fx_max_carry_forward_days') }}
            then f.carried_eur_per_unit
    end as eur_per_unit,

    -- Provenance of the number on this row.
    f.rate_source_date,
    f.days_since_published_rate,
    f.rate_date is not null as is_published_rate,
    f.rate_date is null as is_carried_forward,
    f.days_since_published_rate > {{ var('fx_max_carry_forward_days') }} as is_rate_stale,

    -- Calendar attributes, denormalised off `dim_date` so the common
    -- "rates for 2022" query doesn't need the join.
    f.year,
    f.quarter,
    f.month,
    f.half,
    f.is_weekday
from aged as f
cross join base as b
