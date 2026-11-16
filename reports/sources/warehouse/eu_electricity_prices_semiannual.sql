-- Household electricity prices at Eurostat's own semi-annual grain, for the 41
-- countries it prices (`is_eu_member` marks the EU's 27), from the one
-- non-annual fact in the warehouse (marts schema). The annual column in
-- `emissions_energy` is an average of these.
--
-- Columns are named, not `select *`: the result ships to every visitor's
-- browser, so it carries what the pages read and a new mart column does not
-- join it unasked. A page that needs another column adds it here first.
select
    country_iso3,
    country_name,
    year,
    half,
    period,
    period_start_date,
    electricity_price_eur_kwh,
    change_vs_previous_half_eur_kwh,
    change_vs_previous_half_pct,
    is_eu_member
from marts.fct_eu_electricity_prices_semiannual
