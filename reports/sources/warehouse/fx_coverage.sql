-- How much of the dense daily table is an actual fixing, per year.
--
-- Aggregated here rather than on the page because `fct_fx_rates_daily` is
-- 381,414 rows and none of the detail reaches a chart — the page only ever shows
-- the published / carried / stale split.
select
    year,
    count(*) as n_rows,
    count(distinct currency_code) as n_currencies,
    count(*) filter (where is_published_rate) as n_published,
    count(*) filter (where is_carried_forward and not is_rate_stale) as n_carried,
    count(*) filter (where is_rate_stale) as n_stale
from marts.fct_fx_rates_daily
group by year
