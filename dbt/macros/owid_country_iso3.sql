{#
  OWID's country code, with the one real country it ships without one.

  OWID leaves `iso_code` empty on its aggregates ("World", "Europe (GCP)") and
  on Kosovo, which has no ISO 3166 code. Filtering on a null code therefore
  dropped a country with 31 years of CO2 along with the aggregates, while the
  World Bank, Eurostat and the weather archive all carry it as `XKX` — the
  code `stg_country` uses. The other null-code entities are aggregates or
  states that no longer exist (USSR, Yugoslavia), which no country-year joins.

  One definition, because `stg_co2` and `stg_energy` both clean OWID; a model
  that kept the bare `iso_code` would lose Kosovo again with every test green,
  which is what their unit tests are for.
#}
{% macro owid_country_iso3(iso_code='iso_code', country='country') -%}
    coalesce({{ iso_code }}, case when {{ country }} = 'Kosovo' then 'XKX' end)
{%- endmacro %}
