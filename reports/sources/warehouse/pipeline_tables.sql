-- Row counts and year spans for every table in the modelled layers.
--
-- Columns are named, not `select *`: the result ships to every visitor's
-- browser, so it carries what the pages read and a new mart column does not
-- join it unasked. A page that needs another column adds it here first.
select
    layer,
    table_name,
    rows,
    year_min,
    year_max
from analytics.pipeline_tables
