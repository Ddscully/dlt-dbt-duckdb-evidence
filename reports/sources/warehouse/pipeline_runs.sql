-- What each dbt build cost, by node type, one row per invocation per type.
--
-- Rolled up rather than shipped per node: `analytics.pipeline_runs` is 552 rows
-- per build and grows forever, and the page draws six series. The per-node
-- detail stays in the warehouse for anyone with a question this chart raises.
--
-- `execution_time_s` is dbt's own per-node total and is deliberately not
-- derived from the two phase columns — it exceeds their sum (65.14s against
-- 57.86s on the build this was written against), because dbt counts work
-- outside the compile and execute phases it names.
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
