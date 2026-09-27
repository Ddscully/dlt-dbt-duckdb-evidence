-- The average EU/EEA household electricity price per half-year, in euros and in
-- dollars, over one fixed set of countries: those Eurostat prices in every
-- half-year from 2015-S1 on. An average over whoever reported mixes countries
-- joining with prices moving: coverage grew from 6 countries in 2007-S1 to 38 by
-- 2015, and a panel reaching back that far would keep only 28. The home and
-- currency pages chart this, so their lines and headline rises agree.
with priced as (
    select
        country_iso3,
        period,
        period_start_date,
        electricity_price_eur_kwh,
        electricity_price_usd_kwh,
        usd_per_eur_period_avg
    from marts.fct_eu_electricity_prices_semiannual
    where period >= '2015-S1'
),

panel as (
    select country_iso3
    from priced
    where electricity_price_eur_kwh is not null
      and electricity_price_usd_kwh is not null
    group by country_iso3
    having count(*) = (select count(distinct period) from priced)
)

select
    period,
    period_start_date,
    avg(electricity_price_eur_kwh) as eur_kwh,
    avg(electricity_price_usd_kwh) as usd_kwh,
    min(usd_per_eur_period_avg)    as usd_per_eur,
    count(*)                       as n_countries
from priced
where country_iso3 in (select country_iso3 from panel)
group by period, period_start_date
order by period_start_date
