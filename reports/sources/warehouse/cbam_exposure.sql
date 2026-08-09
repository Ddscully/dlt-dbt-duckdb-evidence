-- CBAM default values resolved to a euro cost per tonne, by sourcing country and
-- good. Unfiltered on purpose: the page's own SQL does the filtering, because a
-- source query that comes back empty is written as a 0-byte parquet and fails the
-- build. The long `goods_description` costs almost nothing here — 264 distinct
-- values over 11,657 rows dictionary-encode away.
select * from marts.fct_cbam_exposure
