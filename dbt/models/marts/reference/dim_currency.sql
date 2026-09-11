-- The currency dimension: the `currencies` seed joined to what the rate series
-- actually contains. Grain: one row per currency_code.
--
-- **The ECB's currency panel is not fixed**: 46 currencies have been quoted and
-- 29 still are. The other seventeen stopped in four shapes:
--
--   * ten at euro adoption (GRD 2000, SIT 2006, CYP and MTL 2007, SKK 2008,
--     EEK 2010, LVL 2013, LTL 2014, HRK 2022, BGN 2025);
--   * two at a redenomination under a new code (TRL -> TRY at 1,000,000:1,
--     ROL -> RON at 10,000:1), so a chart following the code has a cliff;
--   * RUB, last quoted 2022-03-01;
--   * ARS, DZD, MAD and TWD together on 2020-10-30.
--
-- `longest_gap_days` finds a fifth shape, a quote that stopped and resumed: the
-- krona (3,341 days from 2008) and the Argentine peso (34 days in 2002).
--
-- The seed records the twelve euro adoptions and redenominations, which are
-- public record, and deliberately not the publisher's reasons for the other
-- five; `retirement_is_explained` tells the two apart. `first_published_date`
-- and `last_published_date` bound the carry-forward in `fct_fx_rates_daily`.
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
    -- The carry-forward's own threshold, so "current" here and "carried to
    -- today" in `fct_fx_rates_daily` cannot disagree.
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
