-- CO₂ per constant dollar of GDP for six large economies, indexed to 2005 = 100.
-- Constant dollars, never current: see `gdp_constant_usd` in the mart's yml.
--
-- Read by the findings overview and its intensity page.
with base as (
    select country_iso3, co2_per_gdp_const_usd as base_intensity
    from analytics.co2_intensity
    where year = 2005
      and country_iso3 in ('CHN', 'IND', 'USA', 'DEU', 'GBR', 'JPN')
)

select
    i.country_name,
    i.year,
    100 * i.co2_per_gdp_const_usd / b.base_intensity as intensity_index
from analytics.co2_intensity i
inner join base b on i.country_iso3 = b.country_iso3
where i.year >= 2005
  and i.co2_per_gdp_const_usd is not null
order by i.country_name, i.year
