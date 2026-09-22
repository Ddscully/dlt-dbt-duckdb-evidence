-- The World Bank income classification as it stood in each year, beside the one
-- `dim_country` carries today. Why it exists is the description in _reference.yml.
with

history as (
    select * from {{ ref('wb_income_classification') }}
),

countries as (
    select * from {{ ref('dim_country') }}
)

select
    h.country_iso3,
    -- The seed's `data_year` (the GNI year) renamed to the conformed `year`;
    -- otherwise the bus matrix would read this as an orphan.
    h.data_year as year,
    -- The World Bank's own index, two years later: FY27 is the July 2026 release
    -- and classifies on 2025 GNI.
    h.fiscal_year,
    h.income_group,
    h.income_group_code,
    -- `dim_country`'s current answer. Null for territories the World Bank does
    -- not classify and for economies the dimension no longer lists.
    c.income_group as current_income_group,
    -- Never null: false where the current group is unknown, which stays
    -- visible as a null in `current_income_group`.
    coalesce(c.income_group is not null and c.income_group <> h.income_group, false)
        as differs_from_current
from history as h
-- Left: economies no longer listed (the USSR, Yugoslavia, the Netherlands
-- Antilles) still had a group in the years they existed.
left join countries as c on h.country_iso3 = c.country_iso3
