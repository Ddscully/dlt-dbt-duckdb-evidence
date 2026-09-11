"""Where the project's files live.

Every layer asks here for the project root, the DuckDB file and the landing
zone, so they agree — rather than each computing the root from its own location.

## Resolution order for the root

1. ``PROJECT_ROOT``, if set. The explicit answer, and the only one available to a
   consumer that installs this package from somewhere else.
2. The package's own grandparent, when it looks like a project (`src/` layout,
   installed editable — which is this repo).
3. The nearest ancestor of the cwd holding a `pyproject.toml`, for a
   non-editable install where (2) lands in `site-packages`.

The cwd comes last because the Dagster daemon and CLI need not start in the
project directory.

**All three exhausted raises.** A bare cwd fallback would resolve the warehouse
to `./data/warehouse.duckdb`, which DuckDB then *creates*, and the run would go
green against an empty database. The exception names `PROJECT_ROOT`.
"""

from __future__ import annotations

import os
from pathlib import Path

# What steps 2 and 3 look for to decide a directory is the project root.
ROOT_MARKER = "pyproject.toml"

ROOT_ENV_VAR = "PROJECT_ROOT"
WAREHOUSE_ENV_VAR = "WAREHOUSE_PATH"
LAKEHOUSE_ENV_VAR = "LAKEHOUSE_DIR"
CACHE_ENV_VAR = "INGEST_CACHE_DIR"


def _looks_like_root(path: Path) -> bool:
    return (path / ROOT_MARKER).is_file()


def project_root() -> Path:
    """The project directory — the one holding `pyproject.toml`, `dbt/`, `data/`."""
    env = os.environ.get(ROOT_ENV_VAR)
    if env:
        # Taken as given — a consumer's project need not carry this package's
        # marker file — but a path that isn't there is a typo, not a layout.
        root = Path(env).resolve()
        if not root.is_dir():
            raise NotADirectoryError(f"{ROOT_ENV_VAR}={env!r} is not a directory")
        return root

    # src/modern_data_stack/paths.py -> src/modern_data_stack -> src -> the root.
    in_tree = Path(__file__).resolve().parents[2]
    if _looks_like_root(in_tree):
        return in_tree

    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if _looks_like_root(candidate):
            return candidate

    raise RuntimeError(
        f"cannot locate the project root: {Path(__file__).resolve().parents[2]} holds no "
        f"{ROOT_MARKER} and neither does {cwd} or any directory above it. "
        f"Set {ROOT_ENV_VAR} to the directory holding `dbt/` and `data/`."
    )


def warehouse_path() -> str:
    """The DuckDB file every layer reads and writes.

    ``WAREHOUSE_PATH`` overrides it (a fixture run's throwaway file). Set it
    absolute: dbt resolves it from `dbt/`, the Python layers from the root, so a
    relative value names two different warehouses without an error.
    """
    return os.environ.get(WAREHOUSE_ENV_VAR) or str(project_root() / "data" / "warehouse.duckdb")


def lakehouse_dir() -> str:
    """The DuckLake lakehouse — catalog and data files. ``LAKEHOUSE_DIR`` overrides.

    Absolute when set, and stricter than ``WAREHOUSE_PATH``: DuckLake compares
    the recorded data path as a string on every attach, so the same directory
    spelt relative to the root (dlt) and to ``dbt/`` (dbt) is refused.
    """
    return os.environ.get(LAKEHOUSE_ENV_VAR) or str(project_root() / "data" / "lakehouse")


def cache_dir() -> str:
    """Where a source too big to re-fetch per use is kept between runs.

    ``INGEST_CACHE_DIR`` overrides it. Gitignored and safe to delete: it holds
    copies of what a URL still serves, for bulk-file sources that would
    otherwise be re-downloaded per partition.
    """
    return os.environ.get(CACHE_ENV_VAR) or str(project_root() / "data" / "cache")


def dbt_dir() -> Path:
    """The dbt project — also where `profiles.yml` lives, so both dirs match."""
    return project_root() / "dbt"


def dbt_target_path() -> str:
    """The directory dbt writes its artifacts into.

    `DBT_TARGET_PATH` is dbt's own variable for it; `just test-pipeline` sets it
    to keep a fixture build's artifacts out of the real tree.
    `orchestration.assets.dbt_models` passes this directory to `dbt.cli(...)`,
    because dagster-dbt would otherwise write to a unique subdirectory that
    `run_results.json` readers cannot find.
    """
    return os.environ.get("DBT_TARGET_PATH") or str(dbt_dir() / "target")


def dbt_manifest_path() -> str:
    """dbt's manifest, which is only present after a `dbt build` or `dbt parse`.

    Gitignored, so anything reading it has to cope with its absence rather than
    assume a build has happened.
    """
    return os.environ.get("DBT_MANIFEST_PATH") or str(dbt_dir() / "target" / "manifest.json")


def dbt_run_results_path() -> str:
    """dbt's run results, written by every invocation that executes nodes.

    Gitignored. It describes whichever command last executed nodes — a
    `dbt test` overwrites a build's, a `dbt parse` leaves it untouched — which
    is why `observability.build_runs` records `dbt_command` on every row.
    """
    return os.environ.get("DBT_RUN_RESULTS_PATH") or str(dbt_dir() / "target" / "run_results.json")
