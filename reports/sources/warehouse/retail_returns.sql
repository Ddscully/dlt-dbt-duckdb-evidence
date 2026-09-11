-- Returned lines and how confidently each one could be tied back to the sale it
-- reverses. There is no foreign key in the source, so `match_status` is the
-- honest output of an asof join rather than an assertion — the page reports the
-- miss rate instead of hiding it.
--
-- The three columns the page draws, named rather than `select *`: a `select *`
-- here once shipped customer ids to every visitor, and a grep for
-- `customer_id` cannot find a query that names no columns.
select
    match_status,
    return_amount_gbp,
    days_to_return
from marts.fct_retail_returns
