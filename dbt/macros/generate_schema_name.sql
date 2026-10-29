{#
    Use the custom schema name as is ("staging", "core", "marts"),
    instead of dbt's default "<target schema>_<custom schema>" (e.g. "main_staging").
    Adapted from the dbt documentation on custom schemas.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
