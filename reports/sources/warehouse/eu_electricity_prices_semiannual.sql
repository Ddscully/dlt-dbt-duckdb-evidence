-- Household electricity prices at Eurostat's own semi-annual grain, for the 41
-- countries it prices (`is_eu_member` marks the EU's 27), from the one
-- non-annual fact in the warehouse (marts schema). The annual column in
-- `emissions_energy` is an average of these.
select * from marts.fct_eu_electricity_prices_semiannual
