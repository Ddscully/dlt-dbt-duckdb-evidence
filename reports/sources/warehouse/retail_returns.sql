-- Returned lines and how confidently each one could be tied back to the sale it
-- reverses. There is no foreign key in the source, so `match_status` is the
-- honest output of an asof join rather than an assertion — the page reports the
-- miss rate instead of hiding it.
select * from marts.fct_retail_returns
