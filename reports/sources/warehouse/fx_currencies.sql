-- The currency dimension: 45 rows, so no filtering to do. The page reads it for
-- the lifecycle section — which codes stopped, when, and whether the seed has a
-- reason for it.
--
-- Columns are named, not `select *`: the result ships to every visitor's
-- browser, so it carries what the pages read and a new mart column does not
-- join it unasked. A page that needs another column adds it here first.
select
    currency_code,
    currency_name,
    first_published_date,
    last_published_date,
    n_published_days,
    is_quoted,
    is_currently_published,
    longest_gap_days,
    has_interior_gap,
    retired_reason,
    replaced_by_currency,
    retirement_is_explained
from marts.dim_currency
