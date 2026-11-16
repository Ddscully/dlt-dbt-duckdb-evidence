-- The complete country-year spine: every country in the dimension crossed with
-- every year the warehouse covers, whether or not any source reports it.
--
-- The coverage page left-joins the fact onto this, which is the whole reason the
-- spine model exists — a gap becomes a row you can count rather than an absence
-- you have to infer from what isn't there.
--
-- Columns are named, not `select *`: the result ships to every visitor's
-- browser, so it carries what the pages read and a new mart column does not
-- join it unasked. A page that needs another column adds it here first.
select
    country_iso3,
    year
from marts.dim_country_year
