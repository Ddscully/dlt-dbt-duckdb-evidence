-- One row: how much worse the "other countries and territories" fallback is
-- than the median country that *is* listed. This is the mark-up's whole design:
-- being unlisted, or being listed with no value for your good, should cost you.
--
-- Read by the CBAM overview and its by-country page, so it is computed here once.
with fb as (
    select good_key, certificates_2026_t_co2e_per_t as fallback_t
    from marts.fct_cbam_exposure
    where is_fallback_table
),

listed as (
    select good_key, median(certificates_2026_t_co2e_per_t) as median_t
    from marts.fct_cbam_exposure
    where is_country_specific and not is_fallback_table
    group by 1
)

select
    count(*)                                                    as n_goods,
    median(fb.fallback_t / nullif(listed.median_t, 0))          as median_ratio,
    count(*) filter (where fb.fallback_t > listed.median_t)     as n_worse
from fb inner join listed on fb.good_key = listed.good_key
