-- Carbon intensity of each World Bank income group, per year, on two bases.
--
-- `as_classified` groups each country by the income group it held *that year*
-- (`dim_country_income_history`, from 1987, when the classification starts);
-- `today` groups every year by the current classification, which is what the
-- mart's `income_group` column carries. Over thirty years the two are different
-- lists: about half of the economies classified in 1990 are in another group
-- today, including China (low income until 1998) and India (until 2006).
--
-- Both carry the group's intensity two ways. `kg_co2_per_usd` is the ratio of
-- sums — the group's emissions over the group's real GDP, which is what "how
-- much CO₂ a dollar of this group's output carries" means. `mean_country_kg_co2_per_usd`
-- is the unweighted mean of country ratios, which weights Bhutan like China and
-- is kept only so the page can show what the naive chart said.
--
-- Constant 2015 US$ throughout (`gdp_constant_usd`), never current dollars.
with countries as (
    select
        i.country_iso3,
        i.year,
        i.co2_mt,
        i.gdp_constant_usd,
        i.co2_per_gdp_const_usd,
        i.income_group as income_group_today,
        h.income_group as income_group_as_classified
    from analytics.co2_intensity as i
    left join marts.dim_country_income_history as h
        on i.country_iso3 = h.country_iso3 and i.year = h.year
    where i.co2_mt is not null
      and i.gdp_constant_usd > 0
),

both_bases as (
    select
        year,
        'as_classified' as basis,
        income_group_as_classified as income_group,
        co2_mt,
        gdp_constant_usd,
        co2_per_gdp_const_usd
    from countries
    where income_group_as_classified is not null

    union all

    select
        year,
        'today' as basis,
        income_group_today as income_group,
        co2_mt,
        gdp_constant_usd,
        co2_per_gdp_const_usd
    from countries
    where income_group_today is not null
)

select
    year,
    basis,
    income_group,
    count(*) as n_countries,
    sum(co2_mt) as co2_mt,
    sum(gdp_constant_usd) as gdp_constant_usd,
    -- Mt to kg is 1e9, so this is kg CO₂ per constant 2015 US$.
    1e9 * sum(co2_mt) / sum(gdp_constant_usd) as kg_co2_per_usd,
    avg(co2_per_gdp_const_usd) as mean_country_kg_co2_per_usd
from both_bases
group by year, basis, income_group
