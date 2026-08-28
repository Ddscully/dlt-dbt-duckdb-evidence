"""Where the project's files live.

Every layer needs the same few answers — where the project root is, which
DuckDB file to open, where the landing zone goes — and they have to agree,
because dbt resolves its copy of the warehouse path from `dbt/` while the Python
layers resolve theirs from the root.

They agree by all asking here. The alternative, which this repo ran on for a
while, is one layer computing the root from its own location and the rest
importing it: `REPO_ROOT` lived in `ingest/pipeline.py` and meant "the parent of
`ingest/`", so the landing zone, the observability tables, the exporter and the
report builder all took their sense of where the project was from the ingestion
layer. Moving any one of those directories would have repointed the others,
silently.

## Resolution order for the root

1. ``PROJECT_ROOT``, if set. The explicit answer, and the only one available to a
   consumer that installs this package from somewhere else.
2. The package's own grandparent, when it looks like a project (`src/` layout,
   installed editable — which is this repo).
3. The nearest ancestor of the cwd holding a `pyproject.toml`, for a
   non-editable install where (2) lands in `site-packages`.

Steps 2 and 3 are in that order on purpose: the Dagster daemon and the `dagster`
CLI don't necessarily run from the project directory, so a cwd-first search would
make the warehouse path depend on where the process happened to start.

**All three exhausted raises.** Falling back to the cwd is the tempting fourth
step and it fails in the worst available way: a non-editable install started
outside any project tree resolves the warehouse to `./data/warehouse.duckdb`,
DuckDB *creates* that file, and the run goes green against an empty database.
There is no error to read, because nothing went wrong — the answer was just
somewhere else. `PROJECT_ROOT` is the escape hatch, and the exception names it.
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

    ``WAREHOUSE_PATH`` overrides it so a fixture run can target a throwaway file.
    **It must be absolute** when set: dbt resolves its own copy of this from
    `dbt/` (via `profiles.yml`) and the Python layers resolve theirs from the
    root, so a relative override gives you two different warehouses and no error.
    """
    return os.environ.get(WAREHOUSE_ENV_VAR) or str(project_root() / "data" / "warehouse.duckdb")


def lakehouse_dir() -> str:
    """The DuckLake lakehouse — catalog and data files. ``LAKEHOUSE_DIR`` overrides.

    **Absolute when set, and here that is not the convention it is for
    ``WAREHOUSE_PATH``.** DuckLake records the data path it was created with and
    compares it as a *string* on every attach, so the same directory under two
    spellings is refused outright — dlt writes the catalog from the project root
    and dbt resolves its own copy from ``dbt/``, one level down. A plain DuckDB
    file keeps no such record and forgives the difference; this does not.
    """
    return os.environ.get(LAKEHOUSE_ENV_VAR) or str(project_root() / "data" / "lakehouse")


def cache_dir() -> str:
    """Where a source too big to re-fetch per use is kept between runs.

    ``INGEST_CACHE_DIR`` overrides it, as above. Gitignored and safe to delete —
    everything here is a byte-identical copy of something a URL still serves, so
    losing it costs a download and never data. It exists for bulk-drop sources
    that arrive as one file: re-downloading 45 MB once per partition is the
    difference between a backfill you can run and one you won't.
    """
    return os.environ.get(CACHE_ENV_VAR) or str(project_root() / "data" / "cache")


def dbt_dir() -> Path:
    """The dbt project — also where `profiles.yml` lives, so both dirs match."""
    return project_root() / "dbt"


def dbt_manifest_path() -> str:
    """dbt's manifest, which is only present after a `dbt build` or `dbt parse`.

    Gitignored, so anything reading it has to cope with its absence rather than
    assume a build has happened.
    """
    return os.environ.get("DBT_MANIFEST_PATH") or str(dbt_dir() / "target" / "manifest.json")
