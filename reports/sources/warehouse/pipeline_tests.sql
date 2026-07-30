-- One row per dbt test, with the rows currently failing it (0 = passing).
-- Deliberately unfiltered: Evidence cannot write a zero-row source to parquet
-- ("too small to be a Parquet file"), and on a green build the failing subset is
-- empty by definition. The page filters for failures itself.
select * from analytics.pipeline_tests
