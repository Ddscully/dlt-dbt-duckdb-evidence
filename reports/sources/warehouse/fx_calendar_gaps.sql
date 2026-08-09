-- Calendar days against publication days, by day of the week. Seven rows.
--
-- The point it makes is that the missing 30% is not random: Saturday and Sunday
-- are structurally zero, and the weekdays fall a little short of 100% because of
-- the TARGET closures, which no weekday rule predicts.
with published_days as (
    select distinct rate_date from marts.fct_fx_rates_published
),

-- Bounded to the span the series covers, so the leading days of 1999 and the
-- rest of the current year don't count as missed publications.
in_series as (
    select *
    from marts.dim_date
    where date_day between
        (select min(rate_date) from published_days)
        and (select max(rate_date) from published_days)
)

select
    d.day_of_week,
    d.day_name,
    d.is_weekday,
    count(*) as calendar_days,
    count(p.rate_date) as publication_days,
    count(*) - count(p.rate_date) as days_with_no_fixing
from in_series as d
left join published_days as p on d.date_day = p.rate_date
group by d.day_of_week, d.day_name, d.is_weekday
