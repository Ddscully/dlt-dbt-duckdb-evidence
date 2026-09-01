-- The country-years the country-stats sources actually report, one row each.
-- Grain: one row per (country_iso3, year).
--
-- **This exists because two models were deriving it separately from the same
-- four staging models, and disagreeing was a green build.** `dim_country_year`
-- reduced this set to its `min`/`max` year to size the spine;
-- `fct_emissions_energy` kept the pairs to cut the spine's cross join back down
-- to the country-years something was published for. Adding a fifth source to
-- this domain meant the identical edit in both files, and missing either was
-- silent in a different direction — miss the spine's copy and the calendar never
-- reaches the new years, so the fact loses those rows at its inner join; miss
-- the fact's copy and it drops the country-years only the new source reports.
-- Fewer rows, no error, either way. There is one list now, and it is here.
--
-- `year is not null` is `dim_country_year`'s filter, kept because `min(year)`
-- needs it. It changes nothing for the fact: a null key never matched the spine.
with co2 as (
    select
        country_iso3,
        year
    from {{ ref('stg_co2') }}
),

energy as (
    select
        country_iso3,
        year
    from {{ ref('stg_energy') }}
),

wdi as (
    select
        country_iso3,
        year
    from {{ ref('stg_wdi') }}
),

eu_prices as (
    select
        country_iso3,
        year
    from {{ ref('stg_eu_electricity_prices') }}
),

-- `union`, not `union all`: the four overlap heavily and every consumer wants
-- the set rather than the multiset. The `min`/`max` reader is indifferent; the
-- fact's inner join would have to dedupe anyway.
observed as (
    select * from co2
    union
    select * from energy
    union
    select * from wdi
    union
    select * from eu_prices
)

select
    country_iso3,
    cast(year as integer) as year
from observed
where year is not null
