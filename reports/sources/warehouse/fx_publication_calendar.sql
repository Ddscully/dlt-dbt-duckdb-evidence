-- One row per calendar day for the last three complete years, saying whether the
-- ECB published a fixing that day. Feeds the calendar heatmap on the currency
-- page.
--
-- `fx_calendar_gaps` answers "how much of the calendar is missing" by day of the
-- week; this answers "which dates". They are different questions and the second
-- one is the argument for carrying no holiday calendar: the weekday closures are
-- 1 January, Good Friday, Easter Monday, 1 May and 25-26 December, and the
-- Easter pair lands on a different date every year.
--
-- Three complete years rather than the whole 1999-onward series, because the
-- point is that the pattern moves, and 27 years of calendar renders as a wall.
with published_days as (
    select distinct rate_date from marts.fct_fx_rates_published
),

bounds as (
    select date_trunc('year', max(rate_date)) as current_year_start
    from published_days
)

select
    d.date_day,
    d.day_name,
    d.is_weekday,
    case when p.rate_date is null then 0 else 1 end as has_fixing
from marts.dim_date as d
cross join bounds as b
left join published_days as p on d.date_day = p.rate_date
where d.date_day >= b.current_year_start - interval 3 year
  and d.date_day < b.current_year_start
order by d.date_day
