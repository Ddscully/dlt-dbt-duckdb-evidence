-- One row per dbt test, with the rows currently failing it (0 = passing).
-- Deliberately unfiltered: Evidence cannot write a zero-row source to parquet
-- ("too small to be a Parquet file"), and on a green build the failing subset is
-- empty by definition. The page filters for failures itself.
--
-- Columns are named, not `select *`: the result ships to every visitor's
-- browser, so it carries what the pages read and a new mart column does not
-- join it unasked. A page that needs another column adds it here first.
select
    test_name,
    test_type,
    tested_model,
    tested_column,
    failing_rows,
    status,
    audit_table
from analytics.pipeline_tests
