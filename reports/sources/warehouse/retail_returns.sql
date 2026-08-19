-- Returned lines and how confidently each one could be tied back to the sale it
-- reverses. There is no foreign key in the source, so `match_status` is the
-- honest output of an asof join rather than an assertion — the page reports the
-- miss rate instead of hiding it.
--
-- **This was `select *` and shipped 23 columns to render three**, including
-- `customer_id`, `country` and `invoice_ts` — 17,934 clear identifiers over
-- 2,445 customers, downloaded by every visitor to the site. It survived the
-- first pass of the classification work precisely because it is a `select *`:
-- grepping the source queries for `customer_id` does not find a query that
-- names no columns at all. That is the argument for the column list being the
-- default and not the exception.
select
    match_status,
    return_amount_gbp,
    days_to_return
from marts.fct_retail_returns
