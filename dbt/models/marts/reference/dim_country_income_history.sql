-- The World Bank income classification as it stood in each year, next to the one
-- the warehouse carries today.
-- Grain: one row per (country_iso3, year) the World Bank classified.
--
-- **This model exists because every other `income_group` in the warehouse is a
-- Type-1 attribute wearing a time series' clothes.** `dim_country` reads the
-- `/country` endpoint, which publishes only the current answer, so the 2026
-- classification is stamped onto 1990 wherever it is grouped by. Of the
-- economies classified at both ends of this table, 45% are in a different group
-- at the two ends — so "emissions by income group in 1990" is a 2026 grouping of
-- 1990 emissions, which is the "plausible number, wrong basis" class this
-- project catalogues rather than a rounding problem.
--
-- Nothing is renamed or repointed yet: this is the fact published beside the
-- existing column so the distortion is queryable, not a migration of the eight
-- relations that carry `income_group`. `differs_from_current` is what makes it
-- one query rather than a join a reader has to think of.
with

history as (
    select * from {{ ref('wb_income_classification') }}
),

countries as (
    select * from {{ ref('dim_country') }}
)

select
    h.country_iso3,
    -- `year`, not the seed's `data_year`. The seed keeps the publisher's own
    -- word because the workbook draws a real distinction — the GNI *data* year
    -- against the World Bank *fiscal* year the classification governs, which are
    -- two apart — and the conformed key every fact in this warehouse joins on is
    -- spelled `year`. Renaming to the contract on the way through is the same
    -- job staging does for a source; leaving it `data_year` would make this the
    -- one country-year relation the bus matrix reads as an orphan.
    h.data_year as year,
    -- Kept beside it because it is the half a reader needs to check the table
    -- against the World Bank's own published lists, which are indexed by fiscal
    -- year: FY27 is the July 2026 release and classifies on 2025 GNI.
    h.fiscal_year,
    h.income_group,
    h.income_group_code,
    -- What `dim_country` says today, carried on every row so the comparison
    -- needs no join. Null for the `country_overrides` territories the World Bank
    -- does not classify, and for any economy in the history that the dimension
    -- has since dropped — the 2026 file lists 218 where the 2024 one listed 223.
    c.income_group as current_income_group,
    -- Null-safe on purpose: `<>` against a null current classification yields
    -- null, which `where differs_from_current` would then drop silently. A row
    -- whose current group is unknown is *not* a row that agrees, so the column
    -- says false and the null lives in `current_income_group` where a reader
    -- can see it.
    coalesce(c.income_group is not null and c.income_group <> h.income_group, false)
        as differs_from_current
from history as h
-- Left, not inner: the classification history is the World Bank's own record and
-- an economy it has since stopped listing (the USSR, Yugoslavia, Netherlands
-- Antilles) still had an income group in the years it existed. An inner join
-- would delete exactly the rows that make this a history.
left join countries as c on h.country_iso3 = c.country_iso3
