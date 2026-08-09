-- Per-customer RFM scores and segments, from the Polars layer. Shipped at
-- customer grain (5,881 rows) rather than pre-aggregated by segment, so the
-- page can cut it both ways — the segment roll-up and the score distribution
-- behind it.
select * from analytics.retail_rfm
