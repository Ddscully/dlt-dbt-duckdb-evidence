-- Per-customer RFM scores, cut down to what the page draws.
--
-- **This was `select *`, and that shipped 19 columns to build 4.** The extra 15
-- included the customer id, the country and all three dates — every
-- quasi-identifier the table carries — downloaded as a Parquet file by every
-- visitor to the site. Nothing rendered them; they came along because the
-- easiest query is the widest one.
--
-- What the pruning is worth is measured rather than assumed, and it is less than
-- it looks: over the four columns that remain, 5,869 of 5,881 customers (99.8%)
-- are still alone in their combination of values, because `monetary_gbp` is a
-- near-continuous money column at person grain. Removing an identifier is not
-- the same as removing identifiability. What the pruning does buy is real
-- though — nobody can now join this file to anything else, because the columns
-- that would join it are gone.
--
-- The rows stay at customer grain on purpose: the concentration curve on the
-- page is a distribution over 5,881 customers and there is no aggregate that
-- reproduces it. `docs/DATA_PROTECTION.md` carries that decision and the number
-- above.
select
    segment,
    monetary_gbp,
    recency_days,
    frequency
from analytics.retail_rfm
