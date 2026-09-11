-- The newest year of the classification history must agree with the country
-- dimension's current answer, because they are the same classification reached
-- two ways: `dim_country.income_group` is what the World Bank `/country`
-- endpoint serves today, and this table's latest year is the newest column of
-- `OGHIST.xlsx`. Measured at 0 disagreements over 213 economies when it shipped.
--
-- **A staleness detector, hence `warn`.** The World Bank reclassifies every
-- July; the endpoint moves at once and the seed only when
-- `scripts/build_income_classification_seed.py` is re-run, so a disagreement
-- means maintenance is due, not a bug.
--
-- Singular because a generic `expression_is_true` would need the year bound to
-- `ref()` the model, which dbt's config parser refuses ("The macro 'ref' is
-- undefined"). The year is a subquery, never a literal, which would pass forever
-- after the year it named.
{{ config(severity='warn') }}

select
    country_iso3,
    year,
    income_group,
    current_income_group
from {{ ref('dim_country_income_history') }}
where
    year = (select max(year) from {{ ref('dim_country_income_history') }})
    -- Null means the dimension does not carry this economy at all, which is a
    -- different fact from disagreeing about it and is not what this checks.
    and current_income_group is not null
    and differs_from_current
