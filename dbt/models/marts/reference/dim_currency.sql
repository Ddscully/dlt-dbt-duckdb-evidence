-- The currency dimension: the `currencies` seed joined to what the rate series
-- actually contains. Grain: one row per currency_code.
--
-- It exists because **the ECB's currency panel is not fixed**, which is the
-- thing a static list of thirty codes gets wrong. Across the series 46
-- currencies have been quoted and 29 still are. Seventeen stopped, and they
-- stopped in four distinguishable shapes:
--
--   * ten on the last business day before their country adopted the euro
--     (GRD 2000, SIT 2006, CYP and MTL 2007, SKK 2008, EEK 2010, LVL 2013,
--     LTL 2014, HRK 2022, BGN 2025);
--   * two at a redenomination, where the same money continues under a new code
--     (TRL -> TRY at 1,000,000:1, ROL -> RON at 10,000:1) — so a chart of "the
--     Turkish lira" that follows the code rather than the money has a cliff in
--     it in 2005;
--   * one that stops mid-series (RUB, last quoted 2022-03-01);
--   * four that stop together on 2020-10-30 (ARS, DZD, MAD, TWD).
--
-- `longest_gap_days` finds a fifth shape that none of the above covers: a quote
-- that stopped and *resumed*. There are exactly two, and both are currency
-- crises rather than publishing decisions — the Icelandic krona is absent for
-- 3,341 days from the 2008 banking collapse, and the Argentine peso for 34 days
-- from the January 2002 breaking of the dollar peg. A currency's rate series
-- goes quiet when the currency stops working.
--
-- The seed explains the first twelve and **deliberately says nothing about the
-- other five**: it carries the euro adoption and redenomination dates, which are
-- matters of public record and are checked against the data, and it does not
-- guess at a publisher's reason for ceasing a quote. `retirement_is_explained`
-- is how a consumer tells the two apart rather than reading a null as "still
-- current".
--
-- `first_published_date` and `last_published_date` are what bound the carry-
-- forward in `fct_fx_rates_daily`: outside them a currency has no rate to carry,
-- and inventing one would put a euro-era drachma on a chart.
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
        quote_currency,
        min(rate_date) as first_published_date,
        max(rate_date) as last_published_date,
        count(*) as n_published_days
    from published
    group by quote_currency
),

-- The longest run of calendar days between two consecutive fixings. A weekend
-- is 3, the Christmas closures reach 5, and anything far beyond that is the
-- publisher having stopped rather than a holiday — Iceland's krona is 3,341.
gaps as (
    select
        quote_currency,
        max(date_diff('day', previous_rate_date, rate_date)) as longest_gap_days
    from (
        select
            quote_currency,
            rate_date,
            lag(rate_date) over (
                partition by quote_currency
                order by rate_date
            ) as previous_rate_date
        from published
    ) as consecutive
    where previous_rate_date is not null
    group by quote_currency
)

select
    s.currency_code,
    s.currency_name,

    -- Lifetime in the series. Null for EUR, which is the base of every quote and
    -- so never appears as one — the seed carries it because it is a currency,
    -- and `is_quoted` is what keeps it out of anything that ranks rates.
    p.first_published_date,
    p.last_published_date,
    p.n_published_days,
    p.quote_currency is not null as is_quoted,
    -- Tolerant of a currency missing the single newest day: the threshold is the
    -- same one the carry-forward uses, so "current" here and "carried forward to
    -- today" there cannot disagree.
    p.last_published_date
    >= (series.series_end_date - interval {{ var('fx_max_carry_forward_days') }} day)
        as is_currently_published,
    g.longest_gap_days,
    -- A gap far larger than any closure the ECB has ever taken means the quote
    -- stopped and resumed, which the carry-forward must not paper over.
    coalesce(g.longest_gap_days, 0) > {{ var('fx_max_carry_forward_days') }} as has_interior_gap,

    -- What the seed knows and the series can't say.
    s.retired_on,
    s.retired_reason,
    s.replaced_by_currency,
    s.retired_on is not null as retirement_is_explained
from seed as s
left join spans as p on s.currency_code = p.quote_currency
left join gaps as g on s.currency_code = g.quote_currency
cross join series
