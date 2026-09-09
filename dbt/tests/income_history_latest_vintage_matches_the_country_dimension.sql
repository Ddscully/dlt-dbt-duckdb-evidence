-- The newest year of the classification history must agree with the country
-- dimension's current answer, because they are the same classification reached
-- two ways: `dim_country.income_group` is what the World Bank `/country`
-- endpoint serves today, and this table's latest year is the newest column of
-- `OGHIST.xlsx`. Measured at 0 disagreements over 213 economies when it shipped.
--
-- **A staleness detector, which is why the severity is `warn`.** The World Bank
-- reclassifies every July: the endpoint moves to the new fiscal year while the
-- seed sits at the old one, and the economies reclassified in between surface
-- here. Neither number is wrong — they are correct for different vintages — so
-- the finding is "re-run `scripts/build_income_classification_seed.py`", which
-- is maintenance rather than a bug. Same treatment, for the same reason, as
-- `dbt source freshness`: it measures whether somebody ran something.
--
-- **Singular rather than generic, and dbt decided that rather than taste.** As
-- an `expression_is_true` with a `where`, the year bound has to name the model,
-- and the generic-test config parser refuses `ref()` there —
-- "The macro 'ref' is undefined … does not support using custom macros to
-- populate configuration values". The alternative was hardcoding
-- `marts.dim_country_income_history` into a yml, which the style guide forbids
-- precisely because it is invisible to lineage. A singular test resolves `ref`
-- normally, and this is the shape dbt's own guide keeps them for: an assertion
-- about how two models relate.
--
-- The year is a subquery and never a literal. A hardcoded vintage would pin the
-- check to the year it was written in and pass forever after — the trap
-- `stg_wdi`'s `current_date` bound already carries.
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
