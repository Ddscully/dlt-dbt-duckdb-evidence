"""Shared Dagster resources and project handles."""

from __future__ import annotations

from dagster_dbt import DbtCliResource, DbtProject
from dagster_dlt import DagsterDltResource

from modern_data_stack.paths import dbt_dir

DBT_DIR = dbt_dir()

# `profiles.yml` lives next to `dbt_project.yml` in this repo, so both dirs match.
dbt_project = DbtProject(project_dir=DBT_DIR, profiles_dir=DBT_DIR)

# No `prepare_if_dev()`: the `dg` CLI sets the variable it reads for every
# subcommand, so it would run `dbt deps` and `dbt parse` on each `dg launch` and
# `dg list defs`, about 11 s each (docs/decisions/0022). The manifest in
# dbt/target/ has to exist before the graph will load, so every recipe that loads
# it depends on `just dbt-parse`.

RESOURCES = {
    "dbt": DbtCliResource(project_dir=dbt_project),
    "dlt": DagsterDltResource(),
}
