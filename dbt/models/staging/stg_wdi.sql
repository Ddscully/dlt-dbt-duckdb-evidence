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
    max(case when indicator = 'SP.POP.TOTL' then value end) as population,
    max(case when indicator = 'SI.POV.DDAY' then value end) as poverty_rate,
    max(case when indicator = 'IT.NET.USER.ZS' then value end) as internet_users_pct,
    max(case when indicator = 'SP.URB.TOTL.IN.ZS' then value end) as urban_pop_pct,
    max(case when indicator = 'AG.LND.FRST.ZS' then value end) as forest_area_pct,
    max(case when indicator = 'EG.ELC.RNEW.ZS' then value end) as renew_elec_pct,
    max(case when indicator = 'EG.IMP.CONS.ZS' then value end) as energy_imports_pct
from source
where
    country_iso3 is not null
    and length(country_iso3) = 3
    and year is not null
group by country_iso3, year
