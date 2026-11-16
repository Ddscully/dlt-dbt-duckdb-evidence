-- Per-source load times and row counts, written by transform/pipeline_status.py.
-- `loaded_at` is dlt's `_dlt_load_id` (a unix epoch) resolved to a timestamp: it
-- says when this pipeline loaded the data, not when the publisher released it.
--
-- Columns are named, not `select *`: the result ships to every visitor's
-- browser, so it carries what the pages read and a new mart column does not
-- join it unasked. A page that needs another column adds it here first.
select
    source_table,
    rows,
    year_min,
    year_max,
    loaded_at
from analytics.pipeline_sources
