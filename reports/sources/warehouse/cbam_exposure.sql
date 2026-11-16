-- CBAM default values resolved to a euro cost per tonne, by sourcing country and
-- good. Unfiltered on purpose: the page's own SQL does the filtering, because a
-- source query that comes back empty is written as a 0-byte parquet and fails the
-- build. The long `goods_description` costs almost nothing here — 260 distinct
-- values over 11,665 rows dictionary-encode away.
--
-- Columns are named, not `select *`: the result ships to every visitor's
-- browser, so it carries what the pages read and a new mart column does not
-- join it unasked. A page that needs another column adds it here first.
select
    country_or_territory,
    country_iso3,
    country_name,
    country_display_name,
    region,
    product_group,
    cn_code,
    goods_description,
    good_key,
    production_route_code,
    is_fallback_table,
    is_country_specific,
    direct_t_co2e_per_t,
    indirect_t_co2e_per_t,
    total_t_co2e_per_t,
    certificates_2026_t_co2e_per_t,
    markup_2026_pct,
    ets_price_eur_per_t,
    cbam_cost_2026_eur_per_t,
    cbam_cost_2027_eur_per_t,
    cbam_cost_2028_eur_per_t,
    grid_factor_year,
    grid_factor_t_co2_per_mwh
from marts.fct_cbam_exposure
