"""Unit tests for the one module every other layer's file locations come from.

What's worth pinning here isn't the happy path — this repo is an editable `src/`
install, so the in-tree branch always wins and nothing else ever runs. It's the
branches that only fire somewhere else: a consumer setting `PROJECT_ROOT`, and a
non-editable install started outside any project tree, where the old cwd
fallback would have handed back a plausible-looking path to a warehouse that
doesn't exist.
"""

from __future__ import annotations

import pytest

from modern_data_stack import paths


def test_the_in_tree_root_is_this_repo():
    """The branch that actually runs here: `src/modern_data_stack/paths.py`'s
    grandparent, which holds `pyproject.toml`, `dbt/` and `data/`."""
    root = paths.project_root()
    assert (root / "pyproject.toml").is_file()
    assert (root / "dbt" / "dbt_project.yml").is_file()


def test_an_explicit_root_wins(monkeypatch, tmp_path):
    """`PROJECT_ROOT` is the only answer available to a consumer that installs
    this package from outside the tree, so it outranks the in-tree guess even
    when the in-tree guess would have worked.

    The two overrides have to be cleared first, and not clearing them is how this
    test passed locally and failed on the first CI run after `paths.py` landed:
    `ci.yml` exports `WAREHOUSE_PATH`, which correctly beats the root-derived
    default (`test_the_overrides_do_not_need_a_root_at_all` pins that), so the
    assertion below was reading the runner's path instead of `tmp_path`. What is
    under test here is the *derived* location, so the thing that overrides it
    has to be out of the way.
    """
    monkeypatch.delenv(paths.WAREHOUSE_ENV_VAR, raising=False)
    monkeypatch.delenv(paths.LAKE_ENV_VAR, raising=False)
    monkeypatch.setenv(paths.ROOT_ENV_VAR, str(tmp_path))
    assert paths.project_root() == tmp_path.resolve()
    assert paths.warehouse_path() == str(tmp_path / "data" / "warehouse.duckdb")
    assert paths.lake_dir() == str(tmp_path / "data" / "lake")
    assert paths.dbt_dir() == tmp_path / "dbt"


def test_an_explicit_root_that_is_not_there_raises(monkeypatch, tmp_path):
    """A `PROJECT_ROOT` pointing at nothing is a typo, and the alternative is
    every path below it resolving under a directory that doesn't exist."""
    monkeypatch.setenv(paths.ROOT_ENV_VAR, str(tmp_path / "typo"))
    with pytest.raises(NotADirectoryError, match=paths.ROOT_ENV_VAR):
        paths.project_root()


def test_an_explicit_root_need_not_carry_the_marker(monkeypatch, tmp_path):
    """It's taken as given: a consumer's project directory needn't look like
    *this* package's idea of a project, it only has to exist."""
    monkeypatch.setenv(paths.ROOT_ENV_VAR, str(tmp_path))
    assert not (tmp_path / paths.ROOT_MARKER).exists()
    assert paths.project_root() == tmp_path.resolve()


def test_exhausting_the_search_raises_rather_than_using_the_cwd(monkeypatch, tmp_path):
    """The failure the raise replaces: with a cwd fallback, a non-editable
    install started outside any project tree resolves the warehouse to
    `./data/warehouse.duckdb`, DuckDB *creates* it, and the pipeline runs green
    against an empty database. Nothing to read, because nothing raised.
    """
    monkeypatch.delenv(paths.ROOT_ENV_VAR, raising=False)
    monkeypatch.setattr(paths, "_looks_like_root", lambda path: False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(RuntimeError, match=paths.ROOT_ENV_VAR):
        paths.project_root()


def test_the_overrides_do_not_need_a_root_at_all(monkeypatch, tmp_path):
    """`WAREHOUSE_PATH`/`LAKE_DIR` are absolute by contract, so they answer
    without consulting the root — which is what keeps `just test-pipeline`
    working from anywhere."""
    monkeypatch.delenv(paths.ROOT_ENV_VAR, raising=False)
    monkeypatch.setattr(paths, "_looks_like_root", lambda path: False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(paths.WAREHOUSE_ENV_VAR, "/tmp/wh.duckdb")
    monkeypatch.setenv(paths.LAKE_ENV_VAR, "/tmp/lake")

    assert paths.warehouse_path() == "/tmp/wh.duckdb"
    assert paths.lake_dir() == "/tmp/lake"
