-- Twelve invented sites priced against real grid factors — the worked example
-- on the Scope 2 page. The seed behind it is the only fabricated data in this
-- warehouse; see marts/fct_example_scope2_emissions.sql.
--
-- Columns are named, not `select *`: the result ships to every visitor's
-- browser, so it carries what the pages read and a new mart column does not
-- join it unasked. A page that needs another column adds it here first.
select
    site_name,
    site_type,
    country_iso3,
    country_name,
    annual_electricity_mwh,
    factor_year,
    latest_factor_lag_years,
    emission_factor_g_co2_per_kwh,
    scope2_t_co2e,
    share_of_group_pct,
    scope2_at_best_grid_t_co2e,
    scope2_at_worst_grid_t_co2e
from marts.fct_example_scope2_emissions
