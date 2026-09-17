-- Runs once, the first time the `mds_postgres` volume is initialised: the
-- entrypoint ignores this directory on every later start. `just compose-down
-- volumes` is therefore the only way to re-run it.
--
-- `POSTGRES_DB=lakehouse` (compose.yaml) creates the first database; this
-- creates the second. Both are owned by the one role `mds`, so one PGPASSWORD
-- serves the DuckLake catalog and Dagster's run/event storage alike.
--
-- `dagster` is created here although nothing uses it yet: Dagster's storage
-- lands in a later change, and a database that appears only when that lands
-- would need this volume destroyed to get it.
create database dagster owner mds;

comment on database dagster is
    'Dagster run, event and schedule storage. Created empty; Dagster builds its own schema on first connection.';
