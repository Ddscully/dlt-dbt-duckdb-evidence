-- Two averages of the EU household electricity price per half-year, which answer
-- different questions: the second runs 8-17% below the first.
--
-- `eu27_*` is Eurostat's own EU average, each member weighted by its household
-- consumption: what the average household pays, and the only figure here to call
-- "the EU average". `member_mean_eur_kwh` is the plain mean of the members'
-- prices: what the average member country charges.
--
-- Members only, and one fixed set of them: those priced in every half-year from
-- 2008-S1, the first with all of them (2007-S1 has 6). The table also holds
-- countries outside the EU, which are cheaper and would pull a mean over every
-- row well below either line.
--
-- And only the half-years Eurostat has published its aggregate for. A new
-- half-year opens with the early reporters' prices and no EU average, and the
-- panel below would shrink to those reporters for every period: 2026-S1 opened
-- with 12 members, and the plain mean became a mean of 12 back to 2008.
with priced as (
    select
        country_iso3,
        period,
        period_start_date,
        electricity_price_eur_kwh,
        eu27_price_eur_kwh,
        usd_per_eur_period_avg
    from marts.fct_eu_electricity_prices_semiannual
    where is_eu_member
      and period >= '2008-S1'
      and eu27_price_eur_kwh is not null
),

panel as (
    select country_iso3
    from priced
    group by country_iso3
    having count(*) = (select count(distinct period) from priced)
)

select
    period,
    period_start_date,
    min(eu27_price_eur_kwh)                               as eu27_eur_kwh,
    min(eu27_price_eur_kwh) * min(usd_per_eur_period_avg) as eu27_usd_kwh,
    avg(electricity_price_eur_kwh)                        as member_mean_eur_kwh,
    min(usd_per_eur_period_avg)                           as usd_per_eur,
    count(*)                                              as n_members
from priced
where country_iso3 in (select country_iso3 from panel)
group by period, period_start_date
order by period_start_date
