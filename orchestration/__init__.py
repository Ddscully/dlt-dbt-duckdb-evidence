"""Dagster orchestration for the pipeline.

`ingest`, `dbt` and `transform` stay runnable on their own (`just run`); this
package wraps them as software-defined assets so the dependency graph, run
history and freshness state are explicit instead of implied by shell ordering.
"""
