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
    max(case when indicator = 'NY.GDP.MKTP.KD' then value end) as gdp_constant_usd,
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
    -- No future years. This panel is an *observed* one, and on 2026-09-07 it
    -- stopped being only that: SP.POP.TOTL began serving population
    -- projections -- 155 countries, 2031-2050, 3,100 country-years, every
    -- other indicator null -- which failed `year`'s literal 1960-2030 ceiling
    -- and took the nightly, the dashboard deploy and the monthly release with
    -- it. That ceiling was in the wrong place twice over. It could only ever
    -- fail the build, never remove the rows; and anything projected *below*
    -- 2031 would have passed it -- into the spine's `max(year)`, into the
    -- latest-year queries, and into every per-capita join that reads
    -- `population` -- looking exactly like an observation. `current_date`
    -- rather than a literal, which is how the weather and FX date columns
    -- already say this -- and, like those two, it cuts *at* the current year
    -- rather than below it, so a projection for the current year still lands.
    -- That one year is the price of not dropping a genuine current-year
    -- observation, which the World Bank does publish.
    --
    -- The drop is not silent: `pipeline_sources` takes its year span from
    -- `raw` and `pipeline_tables` takes this model's from `staging`, so a
    -- publisher who does it again shows up on the Pipeline page as the two
    -- disagreeing.
    and year <= extract(year from current_date)
group by country_iso3, year
