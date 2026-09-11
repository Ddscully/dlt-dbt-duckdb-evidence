"""Shared Dagster resources and project handles."""

from __future__ import annotations

from dagster_dbt import DbtCliResource, DbtProject
from dagster_dlt import DagsterDltResource

from modern_data_stack.paths import dbt_dir

DBT_DIR = dbt_dir()

# `profiles.yml` lives next to `dbt_project.yml` in this repo, so both dirs match.
dbt_project = DbtProject(project_dir=DBT_DIR, profiles_dir=DBT_DIR)

# Under `dagster dev` only, run `dbt deps` and `dbt parse` on every code-location
# load, so a new model reaches the asset graph without a manual parse. Everywhere
# else the manifest in dbt/target/ has to exist before the graph will load.
dbt_project.prepare_if_dev()

RESOURCES = {
    "dbt": DbtCliResource(project_dir=dbt_project),
    "dlt": DagsterDltResource(),
}
