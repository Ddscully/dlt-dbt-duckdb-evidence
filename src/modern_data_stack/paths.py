"""Where the project's files live.

Every layer needs the same three answers — where the project root is, which
DuckDB file to open, where the lake goes — and they have to agree, because dbt
resolves its copy of the warehouse path from `dbt/` while the Python layers
resolve theirs from the root.

They agree by all asking here. The alternative, which this repo ran on for a
while, is one layer computing the root from its own location and the rest
importing it: `REPO_ROOT` lived in `ingest/pipeline.py` and meant "the parent of
`ingest/`", so the lake, the observability tables, the exporter and the report
builder all took their sense of where the project was from the ingestion layer.
Moving any one of those directories would have repointed the others, silently.

## Resolution order for the root

1. ``PROJECT_ROOT``, if set. The explicit answer, and the only one available to a
   consumer that installs this package from somewhere else.
2. The package's own grandparent, when it looks like a project (`src/` layout,
   installed editable — which is this repo).
3. The nearest ancestor of the cwd holding a `pyproject.toml`, for a
   non-editable install where (2) lands in `site-packages`.
4. The cwd, so nothing raises.

Steps 2 and 3 are in that order on purpose: the Dagster daemon and the `dagster`
CLI don't necessarily run from the project directory, so a cwd-first search would
make the warehouse path depend on where the process happened to start.
"""

from __future__ import annotations

import os
from pathlib import Path

# What steps 2 and 3 look for to decide a directory is the project root.
ROOT_MARKER = "pyproject.toml"

ROOT_ENV_VAR = "PROJECT_ROOT"
WAREHOUSE_ENV_VAR = "WAREHOUSE_PATH"
LAKE_ENV_VAR = "LAKE_DIR"


def _looks_like_root(path: Path) -> bool:
    return (path / ROOT_MARKER).is_file()


def project_root() -> Path:
    """The project directory — the one holding `pyproject.toml`, `dbt/`, `data/`."""
    env = os.environ.get(ROOT_ENV_VAR)
    if env:
        return Path(env).resolve()

    # src/modern_data_stack/paths.py -> src/modern_data_stack -> src -> the root.
    in_tree = Path(__file__).resolve().parents[2]
    if _looks_like_root(in_tree):
        return in_tree

    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if _looks_like_root(candidate):
            return candidate
    return cwd


def warehouse_path() -> str:
    """The DuckDB file every layer reads and writes.

    ``WAREHOUSE_PATH`` overrides it so a fixture run can target a throwaway file.
    **It must be absolute** when set: dbt resolves its own copy of this from
    `dbt/` (via `profiles.yml`) and the Python layers resolve theirs from the
    root, so a relative override gives you two different warehouses and no error.
    """
    return os.environ.get(WAREHOUSE_ENV_VAR) or str(project_root() / "data" / "warehouse.duckdb")


def lake_dir() -> str:
    """The Parquet archive's destination. ``LAKE_DIR`` overrides it, as above."""
    return os.environ.get(LAKE_ENV_VAR) or str(project_root() / "data" / "lake")


def dbt_dir() -> Path:
    """The dbt project — also where `profiles.yml` lives, so both dirs match."""
    return project_root() / "dbt"


def dbt_manifest_path() -> str:
    """dbt's manifest, which is only present after a `dbt build` or `dbt parse`.

    Gitignored, so anything reading it has to cope with its absence rather than
    assume a build has happened.
    """
    return os.environ.get("DBT_MANIFEST_PATH") or str(dbt_dir() / "target" / "manifest.json")
