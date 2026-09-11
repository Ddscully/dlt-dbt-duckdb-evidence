-- What each dbt build cost, by node type, one row per invocation per type.
--
-- Rolled up: `analytics.pipeline_runs` holds hundreds of rows per build and
-- only grows, while the page draws a few series. The per-node detail stays in
-- the warehouse.
--
-- `execution_time_s` is dbt's own total, not the sum of the two phase columns,
-- which it exceeds: dbt counts work outside the phases it names.
select
    invocation_id,
    invocation_started_at,
    dbt_command,
    resource_type,
    count(*)                                    as node_count,
    round(sum(execution_time_s), 2)             as total_s,
    round(sum(coalesce(compile_time_s, 0)), 2)  as compile_s,
    round(max(execution_time_s), 2)             as slowest_node_s
from analytics.pipeline_runs
group by invocation_id, invocation_started_at, dbt_command, resource_type
order by invocation_started_at desc, total_s desc
