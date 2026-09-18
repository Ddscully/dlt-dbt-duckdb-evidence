# 0007. The workflows run the pipeline through `just` and one setup action

Status: accepted 2026-09-01 (#30)

## Context

The four workflows ran `uv run dagster job execute` directly, and each set the
warehouse and landing-zone paths itself. When the landing zone moved into
DuckLake, all four needed the same new line, and none of them got it.

dlt writes the catalog from the repo root and records an absolute `data_path`.
dbt resolved `profiles.yml`'s relative default from `dbt/`, and DuckLake
compares the two **as strings**, so the same directory under two spellings was
refused with `DATA_PATH parameter "../data/lakehouse/data/" does not match
existing data path`. The failure surfaced in `dbt build`, one layer downstream
of the layer that chose the spelling. **No recipe could reproduce it, because
every recipe exported the variable that hid it**: a faithful run had to unset
`LAKEHOUSE_DIR` and work in a clone.

## Decision

`.github/actions/setup` is the one definition of the CI environment: uv, the
venv, `just`, and all three paths (`WAREHOUSE_PATH`, `LAKEHOUSE_DIR`,
`DAGSTER_HOME`), absolute. Every workflow that runs the pipeline uses it and runs
`just` recipes, so a workflow and a laptop run the same command.

## Rejected

- **Keeping the paths in each workflow**, behind an identical six-line comment,
  with `WAREHOUSE_PATH` in `ci.yml` alone. That is the shape that had just cost
  the DuckLake move its fix.
- **Calling `uv` and `dagster` directly because CI lacked `just`.** That blocker
  was already gone: `ci.yml` had installed `just` since `sqlfluff-lint` became a
  local hook whose entry is a recipe.

## Consequences

- Three tests in `tests/test_workflows.py` hold it, and `just` reads the paths
  with `env()` so a pre-set value wins (`the-lakehouse` has both).
- Moving the setup into a composite action took the exactly pinned `setup-uv`
  out of Dependabot's view, since `directory: /` scans `.github/workflows/`
  alone. Dependabot now watches every composite action that pins one, and a
  test holds it to that.
