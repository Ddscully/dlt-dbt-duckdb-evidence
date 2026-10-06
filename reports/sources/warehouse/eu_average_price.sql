-- Two averages of the EU household electricity price per half-year, which answer
-- different questions: the second runs 8-17% below the first.
--
-- `eu27_*` is Eurostat's own EU average, each member weighted by its household
-- consumption: what the average household pays, and the only figure here to call
-- "the EU average". `member_mean_eur_kwh` is the plain mean of the members'
-- prices: what the average member country charges.
--
-- Members only: the table also holds countries outside the EU, which are cheaper
-- and would pull a mean over every row well below either line. And only the
-- half-years every member is priced in and Eurostat has published its aggregate
-- for, so the plain mean is over the same 27 in every period. That starts at
-- 2008-S1 (2007-S1 has 6), and leaves out a half-year still being published:
-- 2026-S1 opened with 12 members and no aggregate.
--
-- The rule is per half-year on purpose. A fixed panel of members priced in every
-- period, which this was, drops a member from the whole history for one missing
-- half-year: the 12 early reporters of 2026-S1 became the panel back to 2008.
with members as (
    select
        country_iso3,
        period,
        period_start_date,
        electricity_price_eur_kwh,
        eu27_price_eur_kwh,
        usd_per_eur_period_avg
    from marts.fct_eu_electricity_prices_semiannual
    where is_eu_member
),

complete as (
    select period
    from members
    where eu27_price_eur_kwh is not null
    group by period
    having count(*) = (select count(distinct country_iso3) from members)
)

select
    period,
    period_start_date,
    min(eu27_price_eur_kwh)                               as eu27_eur_kwh,
    min(eu27_price_eur_kwh) * min(usd_per_eur_period_avg) as eu27_usd_kwh,
    avg(electricity_price_eur_kwh)                        as member_mean_eur_kwh,
    min(usd_per_eur_period_avg)                           as usd_per_eur,
    count(*)                                              as n_members
from members
where period in (select period from complete)
group by period, period_start_date
order by period_start_date
