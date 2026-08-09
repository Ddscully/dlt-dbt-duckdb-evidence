-- Period aggregates for every currency, all four period types (~20k rows). Left
-- unfiltered so the page's dropdown can move between currencies and between
-- monthly and annual grains without a source per combination — and because a
-- source query that comes back empty is written as a 0-byte parquet and fails
-- the build.
select * from marts.fct_fx_rates_periods
