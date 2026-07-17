-- World Bank WDI, pivoted from long (indicator, value) to one row per
-- (country iso, year) with a column per indicator.
with source as (
    select * from {{ source('raw', 'wb_wdi') }}
)

select
    country_iso3,
    year,
    max(case when indicator = 'NY.GDP.PCAP.CD' then value end) as gdp_per_capita_usd,
    max(case when indicator = 'NY.GDP.MKTP.CD' then value end) as gdp_usd,
    max(case when indicator = 'SP.DYN.LE00.IN' then value end) as life_expectancy,
    max(case when indicator = 'SP.POP.TOTL'   then value end) as population,
    max(case when indicator = 'SI.POV.DDAY'   then value end) as poverty_rate
from source
where country_iso3 is not null
  and length(country_iso3) = 3
  and year is not null
group by country_iso3, year
