-- One row: the latest year each metric family can actually populate.
--
-- Pages read this instead of hardcoding a year literal, so the site dates itself
-- from the data rather than from whenever someone last edited the markdown.
--
-- Not `max(year)`: the mart's max year is whichever source runs furthest ahead,
-- and coverage falls off unevenly — `primary_energy_twh` drops from about 210
-- countries to 79 in a year where `co2_mt` still has 214. Each family gets the
-- latest year that still has a broad enough sample to chart.
--
-- `*_label` copies exist because a year rendered through Evidence's numeric
-- formatter comes out as "2,024".
with coverage as (
    select
        year,
        count(co2_mt)                      as n_co2,
        count(primary_energy_twh)          as n_energy,
        count(carbon_intensity_elec_g_kwh) as n_elec,
        count(gdp_constant_usd)            as n_gdp,
        count(consumption_co2)             as n_consumption,
        -- Complete years only: the annual price averages whichever halves
        -- exist, so the newest year can be January-June alone.
        count(electricity_price_eur_kwh) filter (
            where not price_is_partial_year
        ) as n_price
    from marts.fct_emissions_energy
    group by year
),

latest as (
    select
        max(year) filter (where n_co2 >= 200)         as co2_year,
        max(year) filter (where n_energy >= 200)      as energy_year,
        max(year) filter (where n_elec >= 150)        as elec_year,
        max(year) filter (where n_gdp >= 190)         as gdp_year,
        max(year) filter (where n_consumption >= 100) as consumption_year,
        max(year) filter (where n_price >= 25)        as price_year
    from coverage
)

select
    co2_year,
    energy_year,
    elec_year,
    gdp_year,
    consumption_year,
    price_year,
    cast(co2_year as varchar)         as co2_year_label,
    cast(energy_year as varchar)      as energy_year_label,
    cast(elec_year as varchar)        as elec_year_label,
    cast(gdp_year as varchar)         as gdp_year_label,
    cast(consumption_year as varchar) as consumption_year_label,
    cast(price_year as varchar)       as price_year_label
from latest
