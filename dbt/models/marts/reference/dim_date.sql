-- The calendar. One row per day, dense, no gaps by construction.
-- Grain: one row per date_day.
--
-- So that "which quarter", "is this a weekday" and "which fiscal year" are a
-- join answered identically everywhere, not an expression re-derived per query.
--
-- 1. **The span comes from the data**: whole calendar years around the FX
--    series, the daily data the warehouse has. The annual models join on
--    `year`, not a date.
-- 2. **ISO year is not calendar year.** 2021-01-01 is in ISO week 53 of 2020 and
--    2019-12-30 in ISO week 1 of 2020, so pair `iso_week` with `iso_year` (or use
--    `iso_week_start_date`), never with `year`.
-- 3. **The fiscal columns are a policy** from the `fiscal_year_start_month` var,
--    carried on every row. `fiscal_year` is the calendar year the fiscal year
--    ends in, so it equals `year` when the var is 1.
with bounds as (
    select
        date_trunc('year', min(rate_date)) as first_day,
        -- Last day of the year the series reaches into, so a partial current
        -- year still gets a whole calendar behind it.
        (date_trunc('year', max(rate_date)) + interval 1 year) - interval 1 day as last_day
    from {{ ref('stg_fx_rates') }}
),

days as (
    select cast(unnest(generate_series(first_day, last_day, interval 1 day)) as date) as date_day
    from bounds
),

parts as (
    select
        date_day,
        cast(date_part('year', date_day) as integer) as year,
        cast(date_part('quarter', date_day) as integer) as quarter,
        cast(date_part('month', date_day) as integer) as month,
        cast(date_part('day', date_day) as integer) as day_of_month,
        cast(date_part('dayofyear', date_day) as integer) as day_of_year,
        cast(date_part('isoyear', date_day) as integer) as iso_year,
        cast(date_part('week', date_day) as integer) as iso_week,
        cast(isodow(date_day) as integer) as day_of_week,
        {{ var('fiscal_year_start_month') }} as fiscal_year_start_month
    from days
),

fiscal as (
    select
        *,
        -- The fiscal year containing this day starts on the most recent
        -- occurrence of `fiscal_year_start_month` — this year's if we are past
        -- it, last year's if we are not.
        case
            when month >= fiscal_year_start_month
                then make_date(year, fiscal_year_start_month, 1)
            else make_date(year - 1, fiscal_year_start_month, 1)
        end as fiscal_year_start_date
    from parts
)

select
    date_day,
    -- The integer surrogate (yyyymmdd): sorts and reads like the date.
    cast(strftime(date_day, '%Y%m%d') as integer) as date_key,

    -- Calendar
    year,
    quarter,
    month,
    day_of_month,
    day_of_year,
    monthname(date_day) as month_name,
    date_trunc('year', date_day) as year_start_date,
    (date_trunc('year', date_day) + interval 1 year) - interval 1 day as year_end_date,
    date_trunc('quarter', date_day) as quarter_start_date,
    (date_trunc('quarter', date_day) + interval 3 month) - interval 1 day as quarter_end_date,
    date_trunc('month', date_day) as month_start_date,
    last_day(date_day) as month_end_date,

    -- Half-years, spelled the way Eurostat spells them ('S1'/'S2'), so the
    -- semi-annual electricity price joins this without a translation step.
    case when month <= 6 then 'S1' else 'S2' end as half,
    make_date(year, case when month <= 6 then 1 else 7 end, 1) as half_start_date,

    -- ISO weeks. `iso_year` travels with `iso_week` or the pair is wrong at both
    -- ends of the year — see the header.
    iso_year,
    iso_week,
    iso_year || '-w' || lpad(cast(iso_week as varchar), 2, '0') as iso_week_label,
    cast(date_day - (day_of_week - 1) as date) as iso_week_start_date,

    -- Generic weekday flags, not a market calendar: TARGET closures show up as
    -- absences in `fct_fx_rates_daily` rather than being asserted here.
    day_of_week,
    dayname(date_day) as day_name,
    day_of_week <= 5 as is_weekday,
    day_of_week >= 6 as is_weekend,

    -- Fiscal. Carried with the policy that produced it.
    fiscal_year_start_month,
    fiscal_year_start_date,
    (fiscal_year_start_date + interval 1 year) - interval 1 day as fiscal_year_end_date,
    cast(date_part('year', (fiscal_year_start_date + interval 1 year) - interval 1 day) as integer)
        as fiscal_year,
    -- `floor`: DuckDB's `/` is float division and the integer cast rounds, so
    -- 11/3 + 1 would give March a fiscal quarter 5 under an April start.
    cast(floor(((month - fiscal_year_start_month + 12) % 12) / 3) + 1 as integer) as fiscal_quarter,
    cast(((month - fiscal_year_start_month + 12) % 12) + 1 as integer) as fiscal_month
from fiscal
