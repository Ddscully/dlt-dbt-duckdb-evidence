-- The euro reference rates on every calendar day, the last fixing carried
-- forward and capped at `fx_max_carry_forward_days`. The why, and what not to do
-- with it, are the description in _reference.yml.
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

-- One row per currency per day within its quoted lifetime, not a full cross
-- join over every currency and date.
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
            s.currency_code = p.currency_code
            and s.date_day = p.rate_date
),

filled as (
    select
        *,
        last_value(units_per_eur ignore nulls) over carried as carried_units_per_eur,
        last_value(eur_per_unit ignore nulls) over carried as carried_eur_per_unit,
        last_value(rate_date ignore nulls) over carried as rate_source_date
    from observed
    -- Up to the current row only, so a day never takes a later fixing.
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
