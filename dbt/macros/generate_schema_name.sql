{#
  Use the custom schema name verbatim (e.g. `staging`, `marts`) instead of
  dbt's default `<target_schema>_<custom>` (which gave `main_staging`, `main_marts`).
  Models with no +schema fall back to the target schema (`main`).
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
