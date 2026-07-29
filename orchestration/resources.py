"""Shared Dagster resources and project handles."""

from __future__ import annotations

from pathlib import Path

from dagster_dbt import DbtCliResource, DbtProject
from dagster_dlt import DagsterDltResource

REPO_ROOT = Path(__file__).resolve().parent.parent
DBT_DIR = REPO_ROOT / "dbt"

# `profiles.yml` lives next to `dbt_project.yml` in this repo, so both dirs match.
dbt_project = DbtProject(project_dir=DBT_DIR, profiles_dir=DBT_DIR)

# Under `dagster dev`, re-parse the dbt project on every code-location reload so
# a newly added model shows up in the asset graph without a manual `dbt parse`.
dbt_project.prepare_if_dev()

RESOURCES = {
    "dbt": DbtCliResource(project_dir=dbt_project),
    "dlt": DagsterDltResource(),
}
