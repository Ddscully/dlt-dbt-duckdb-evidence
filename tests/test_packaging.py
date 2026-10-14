"""How the project is built: where `src/modern_data_stack/` is spelled, and which
uv builds it.

The directory is on disk, `[tool.uv.build-backend] module-name` is read by
uv_build at install time, and `[project.scripts]`'s entry point is resolved only
when someone runs `mds`. Nothing makes the three agree, and all three are
invisible until a rename — which is what `docs/REUSING_THIS_STACK.md` §5 is for,
and why the coupling they describe is worth a test rather than a paragraph.
"""

from __future__ import annotations

import re
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


IMAGES = ("Dockerfile", ".devcontainer/Dockerfile")


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def test_the_build_backend_admits_the_uv_the_images_run():
    """`[build-system]`'s `uv_build` range and the images' uv pin move separately.

    Dependabot bumps the `ghcr.io/astral-sh/uv` tag and never the range. A uv
    outside the range does not fail: it downloads `uv_build` from PyPI instead
    of using the backend built into it, so the image keeps building and nothing
    says the pin is stale. uv's 0.13 release notes ask for exactly this upper
    bound to be raised. Held here so the Dependabot PR that moves uv past the
    range is the one that goes red.
    """
    (requirement,) = [
        r for r in _pyproject()["build-system"]["requires"] if r.startswith("uv_build")
    ]
    bounds = re.findall(r"(>=|<=|<|>|==)\s*([0-9][0-9.]*)", requirement)
    assert bounds, f"no version bounds read from {requirement!r}"
    ops = {
        ">=": lambda v, b: v >= b,
        "<=": lambda v, b: v <= b,
        "<": lambda v, b: v < b,
        ">": lambda v, b: v > b,
        "==": lambda v, b: v == b,
    }
    for image in IMAGES:
        text = (project_root() / image).read_text()
        (pin,) = re.findall(r"ghcr\.io/astral-sh/uv:([0-9][0-9.]*)", text)
        outside = [
            f"{op}{bound}" for op, bound in bounds if not ops[op](_version(pin), _version(bound))
        ]
        assert not outside, (
            f"{image} runs uv {pin}, outside `{requirement}` ({', '.join(outside)}): "
            "widen the range in pyproject.toml's [build-system]"
        )
