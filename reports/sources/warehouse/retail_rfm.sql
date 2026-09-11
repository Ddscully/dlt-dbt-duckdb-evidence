-- Per-customer RFM scores, cut down to what the page draws.
--
-- Only the columns the page draws: no customer id, country or dates, which every
-- visitor would otherwise download. That stops the file joining to anything
-- else, but not identifiability — 99.8% of the 5,881 customers are still unique
-- on these four columns, `monetary_gbp` being near-continuous. Customer grain is
-- kept because the concentration curve is a distribution over customers
-- (`docs/DATA_PROTECTION.md` records the decision).
select
    segment,
    monetary_gbp,
    recency_days,
    frequency
from analytics.retail_rfm
