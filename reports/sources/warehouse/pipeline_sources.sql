-- Per-source load times and row counts, written by transform/pipeline_status.py.
-- `loaded_at` is dlt's `_dlt_load_id` (a unix epoch) resolved to a timestamp: it
-- says when this pipeline loaded the data, not when the publisher released it.
select * from analytics.pipeline_sources
