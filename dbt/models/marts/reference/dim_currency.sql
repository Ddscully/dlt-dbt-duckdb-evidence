-- The `currencies` seed joined to what the rate series contains. The panel is
-- not fixed, and how codes stopped is the description in _reference.yml and the
-- `currency-and-calendar` skill; which ones is the seed.
with seed as (
    select * from {{ ref('currencies') }}
),

published as (
    select * from {{ ref('fct_fx_rates_published') }}
),

series as (
    select max(rate_date) as series_end_date
    from published
),

spans as (
    select
        currency_code,
        min(rate_date) as first_published_date,
        max(rate_date) as last_published_date,
        count(*) as n_published_days
    from published
    group by currency_code
),

-- The longest run of calendar days between consecutive fixings: 3 for a
-- weekend, up to 5 at Christmas, far more when the quote stopped.
gaps as (
    select
        currency_code,
        max(date_diff('day', previous_rate_date, rate_date)) as longest_gap_days
    from (
        select
            currency_code,
            rate_date,
            lag(rate_date) over (
                partition by currency_code
                order by rate_date
            ) as previous_rate_date
        from published
    ) as consecutive
    where previous_rate_date is not null
    group by currency_code
)

select
    s.currency_code,
    s.currency_name,

    -- Null for EUR, the base of every quote; `is_quoted` keeps it out of
    -- anything that ranks rates.
    p.first_published_date,
    p.last_published_date,
    p.n_published_days,
    p.currency_code is not null as is_quoted,
    -- The carry-forward's threshold, so this and `fct_fx_rates_daily` agree.
    p.last_published_date
    >= (series.series_end_date - interval {{ var('fx_max_carry_forward_days') }} day)
        as is_currently_published,
    g.longest_gap_days,
    -- A gap longer than the carry allows: the quote stopped and resumed.
    coalesce(g.longest_gap_days, 0) > {{ var('fx_max_carry_forward_days') }} as has_interior_gap,

    -- What the seed knows and the series can't say.
    s.retired_on,
    s.retired_reason,
    s.replaced_by_currency,
    s.retired_on is not null as retirement_is_explained
from seed as s
left join spans as p on s.currency_code = p.currency_code
left join gaps as g on s.currency_code = g.currency_code
cross join series
