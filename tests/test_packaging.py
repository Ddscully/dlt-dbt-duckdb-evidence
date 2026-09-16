"""The three places `src/modern_data_stack/` is spelled, and what checks them.

The directory is on disk, `[tool.uv.build-backend] module-name` is read by
uv_build at install time, and `[project.scripts]`'s entry point is resolved only
when someone runs `mds`. Nothing makes the three agree, and all three are
invisible until a rename — which is what `docs/REUSING_THIS_STACK.md` §5 is for,
and why the coupling they describe is worth a test rather than a paragraph.
"""

from __future__ import annotations

import tomllib

import modern_data_stack
from modern_data_stack.paths import project_root

PACKAGE = "modern_data_stack"


def _pyproject() -> dict:
    with (project_root() / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def test_the_package_name_is_set_rather_than_derived():
    """Deleting `module-name` is the regression here, and it is silent.

    uv_build then derives the module from `[project] name`, which still agrees
    with the directory today, so the build goes on working. It fails the first
    time someone renames the project — possibly years later, possibly in a fork
    — with an error about `src/` that names neither this key nor the rename.
    """
    config = _pyproject()["tool"]["uv"].get("build-backend", {})
    assert config.get("module-name") == PACKAGE
    assert (project_root() / "src" / PACKAGE / "__init__.py").is_file()


def test_the_console_script_names_a_callable_in_that_package():
    """`mds = "modern_data_stack:main"` is resolved when someone runs `mds`, not
    when the package is installed, so a rename that misses it breaks for a user
    and for no one else. §5 lists it as one of the two places a scan of `*.py`
    does not reach."""
    module, _, attribute = _pyproject()["project"]["scripts"]["mds"].partition(":")
    assert module == PACKAGE
    assert callable(getattr(modern_data_stack, attribute))
