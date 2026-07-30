-- EU/EEA household electricity prices at Eurostat's own semi-annual grain, from
-- the one non-annual fact in the warehouse (marts schema). The annual column in
-- `emissions_energy` is an average of these.
select * from marts.fct_eu_electricity_prices_semiannual
