"""Every asset and check in `assets.py` is registered in `definitions.py`.

`dg.Definitions` takes an explicit list, and nothing complains about an omission:
the asset simply isn't in the graph, so `AssetSelection.all()` never sees it and
`dagster definitions validate` passes. An unlisted asset fails somewhere
downstream; an unlisted check fails *silently*, because it just never runs.

The explicit list stays (it is the one place that says what the graph is); this
is what makes forgetting it loud.
"""

from __future__ import annotations

import dagster as dg
import pytest

from orchestration.resources import dbt_project

# `just test` runs before `dbt deps && dbt parse` in ci.yml, and importing
# orchestration.assets needs the manifest parse writes; CI re-runs this file after.
pytestmark = pytest.mark.skipif(
    not dbt_project.manifest_path.exists(),
    reason="needs dbt/target/manifest.json — run `just dbt-deps` and `dbt parse` first",
)

# The dlt-pipeline-deactivation fixture this file needs (importing orchestration
# leaves a dlt pipeline active process-wide) lives in conftest.py, shared with
# test_asset_checks.py.


def _defined_in_assets_module():
    """(asset keys, check keys) declared at module level in `assets.py`."""
    from orchestration import assets

    asset_keys: set[dg.AssetKey] = set()
    check_keys: set[dg.AssetCheckKey] = set()
    for value in vars(assets).values():
        # AssetChecksDefinition subclasses AssetsDefinition, so this order is
        # load-bearing — reversed, every check falls into the first branch and
        # the set comes out empty.
        if isinstance(value, dg.AssetChecksDefinition):
            check_keys.update(value.check_keys)
        elif isinstance(value, dg.AssetsDefinition):
            asset_keys.update(value.keys)
    return asset_keys, check_keys


def test_every_asset_defined_is_in_the_graph():
    from orchestration.definitions import defs

    defined, _ = _defined_in_assets_module()
    # Executable, not get_all_asset_keys(): an unregistered dependency still
    # shows up as an external node, so the wider set can report a key present
    # purely because something names it in `deps`.
    in_graph = defs.resolve_asset_graph().executable_asset_keys

    missing = defined - in_graph
    assert not missing, (
        "assets defined in orchestration/assets.py but not listed in "
        f"orchestration/definitions.py: {sorted(k.to_user_string() for k in missing)}"
    )


def test_every_asset_check_defined_is_in_the_graph():
    from orchestration.definitions import defs

    _, defined = _defined_in_assets_module()
    in_graph = set(defs.resolve_asset_graph().asset_check_keys)

    missing = defined - in_graph
    assert not missing, (
        "asset checks defined in orchestration/assets.py but not listed in "
        f"orchestration/definitions.py: {sorted(k.to_user_string() for k in missing)}"
    )


def test_every_raw_resource_has_an_asset_description():
    """`RAW_DESCRIPTIONS` re-enumerates the dlt resources, and cannot be derived:
    the prose is not computable from the source, so it is held to it instead.

    `assets.py` reads it as `RAW_DESCRIPTIONS.get(name)`, which returns `None`
    for an unlisted resource. The asset then materialises with no description
    and nothing is red: the Dagster UI shows a blank where every sibling has a
    sentence.

    Lives here rather than in `tests/test_ingest.py` because reading the dict
    means importing `orchestration.assets`, which needs dagster (an optional
    group) and the manifest; this module already carries that skip and is
    re-run by CI after `dbt parse`.
    """
    from ingest import pipeline
    from orchestration.assets import RAW_DESCRIPTIONS

    resources = {r.name for r in pipeline.public_indicators().resources.values()}

    # Separate assertions, not one set equality: they catch different bugs, and
    # the message should say which.
    undescribed = resources - RAW_DESCRIPTIONS.keys()
    assert not undescribed, (
        "dlt resources with no entry in orchestration/assets.py RAW_DESCRIPTIONS "
        f"(they materialise with no description): {sorted(undescribed)}"
    )

    # The reverse direction nothing else could surface: `.get()` never consults
    # an unmatched key, so a stale entry from a rename is invisible.
    orphaned = RAW_DESCRIPTIONS.keys() - resources
    assert not orphaned, (
        "RAW_DESCRIPTIONS entries naming no dlt resource — renamed or removed "
        f"upstream and left behind here: {sorted(orphaned)}"
    )

    # A key check alone accepts an empty string, indistinguishable from missing;
    # length, not truthiness, since `" "` is falsy nowhere but blank everywhere.
    blank = sorted(name for name, text in RAW_DESCRIPTIONS.items() if not text.strip())
    assert not blank, f"RAW_DESCRIPTIONS entries that render blank: {blank}"


def _job_keys(name: str) -> set[dg.AssetKey]:
    """The assets a job actually *materializes*.

    Not `get_all_asset_keys()` — a job's graph also carries the upstream assets it
    only reads, as unexecutable nodes. `raw/retail_invoice_lines` appears in
    `full_refresh` that way (the dbt models depend on it) even though the whole
    point of the selection is that this job does not load it, so the wider set
    would have made the exclusion test pass while asserting nothing.
    """
    from orchestration.definitions import defs

    return set(defs.resolve_job_def(name).asset_layer.asset_graph.executable_asset_keys)


def test_the_jobs_between_them_cover_every_asset():
    """Registered is not the same as reachable, and the second one is what runs.

    `full_refresh` excludes the retail *ingest* — it has to, or the job would be
    month-partitioned (below) — so the exclusion has to be paid for by
    `load_retail` rather than dropped. Anything in neither job is built by no
    workflow.
    """
    from orchestration import assets

    defined, _ = _defined_in_assets_module()
    covered = _job_keys("full_refresh") | _job_keys("load_retail")

    assert defined - covered == {assets.EVIDENCE_SITE}, (
        "the Evidence site is the only asset no pure-Python job may build; "
        f"unreachable: {sorted(k.to_user_string() for k in defined - covered)}"
    )
    assert assets.EVIDENCE_SITE in _job_keys("publish_site")


def test_retail_ingest_is_the_only_thing_full_refresh_leaves_out():
    """Excluded on purpose, and only the one asset.

    `AssetSelection.all() - site - retail_ingest` subtracts a multi-asset's keys;
    a source added to `PARTITIONED_RESOURCES` would join that block and be
    dropped from `full_refresh` silently.
    """
    from orchestration import assets

    retail_keys = set(assets.raw_retail_asset.keys)
    assert retail_keys == {dg.AssetKey(["raw", "retail_invoice_lines"])}
    assert not retail_keys & _job_keys("full_refresh")
    assert retail_keys == _job_keys("load_retail")

    # The downstream retail models are unpartitioned and must stay in the graph.
    assert dg.AssetKey(["analytics", "retail_rfm"]) in _job_keys("full_refresh")


def test_the_routine_jobs_are_not_partitioned():
    """A job takes its partitions from its assets, and a partitioned job's
    Materialize button is a backfill (`docs/decisions/0002`)."""
    from orchestration.definitions import defs

    for name in ("full_refresh", "publish_site"):
        partitions = defs.resolve_job_def(name).partitions_def
        assert partitions is None, (
            f"`{name}` is partitioned ({type(partitions).__name__}), so its Materialize "
            "button in the UI is a backfill of every partition. Keep the partitioned "
            "asset out of the selection, as `load_retail` does for retail, or give it "
            "run config instead (`YearRange` in orchestration/assets.py)."
        )


def test_the_backfill_recipes_address_the_op_that_takes_the_years():
    """The backfill recipes spell the op name their year config is addressed to."""
    import re

    from modern_data_stack.paths import project_root
    from orchestration.assets import raw_by_year_assets

    justfile = (project_root() / "justfile").read_text()
    for recipe in ("backfill-wdi", "backfill-weather"):
        # The header line, then every indented or blank line under it.
        body = re.search(rf"^{recipe} [^\n]*\n((?:[ \t][^\n]*\n|\n)*)", justfile, re.MULTILINE)
        assert body, f"no `{recipe}` recipe in the justfile"
        addressed = re.findall(r'"ops": \{"([\w-]+)"', body[1])
        assert addressed == [raw_by_year_assets.op.name], (
            f"`just {recipe}` addresses its year config to {addressed}, but the op "
            f"that reads it is `{raw_by_year_assets.op.name}`. `dagster asset "
            "materialize` ignores config for an op it does not know, so the recipe "
            "would load the incremental lookback and report success."
        )


def _this_year() -> int:
    from datetime import UTC, datetime

    return datetime.now(UTC).year


def test_a_year_range_resolves_to_the_closed_range_it_names():
    """Unset is the lookback, one field is one year, and both bounds are loadable.

    `just materialize` never passes config, so CI never calls this method; only a
    backfill does, against WDI and weather, the two sources that still move.
    """
    from orchestration.assets import WDI_FIRST_YEAR, YearRange

    this_year = _this_year()
    assert YearRange().years() is None
    assert YearRange(first_year=2020).years() == (2020, 2020)
    assert YearRange(first_year=2015, last_year=2020).years() == (2015, 2020)
    # Both ends inclusive: the floor is WDI's first year, the ceiling this one.
    assert YearRange(first_year=WDI_FIRST_YEAR, last_year=this_year).years() == (
        WDI_FIRST_YEAR,
        this_year,
    )


def test_a_year_range_refuses_what_would_load_nothing_or_projections():
    """Each refusal stands in for a run that would report success.

    A backwards range loads no rows, a year past this one loads World Bank
    projections that `stg_wdi` then cuts, and `last_year` alone would silently
    fall back to the lookback.
    """
    from orchestration.assets import WDI_FIRST_YEAR, YearRange

    this_year = _this_year()
    refused = {
        "last_year without first_year": YearRange(last_year=2020),
        "backwards": YearRange(first_year=2020, last_year=2015),
        "before WDI's first year": YearRange(first_year=WDI_FIRST_YEAR - 1),
        "past this year": YearRange(first_year=this_year, last_year=this_year + 1),
    }
    for case, config in refused.items():
        with pytest.raises(ValueError):
            config.years()
            pytest.fail(f"YearRange accepted a range it must refuse: {case}")


def test_every_retail_month_has_a_partition_to_land_in():
    """The partitions cover the closed interval the retail constants describe."""
    from ingest.sources.retail import RETAIL_FIRST_MONTH, RETAIL_LAST_MONTH
    from orchestration.assets import RETAIL_PARTITIONS

    keys = RETAIL_PARTITIONS.get_partition_keys()
    assert keys[0] == RETAIL_FIRST_MONTH
    assert keys[-1] == RETAIL_LAST_MONTH, (
        f"the last partition is {keys[-1]}, not {RETAIL_LAST_MONTH}: the partitions "
        "definition's `end` is exclusive, so it takes the month after the last one, "
        "or a backfill never reaches that month's lines"
    )
    # 2009-12 through 2011-12 inclusive, i.e. no gaps in between either.
    assert len(keys) == 25


# --------------------------------------------------------------------------- #
# dbt_models — where the build leaves its artifacts
# --------------------------------------------------------------------------- #


def test_the_dbt_build_writes_its_run_results_where_the_reader_looks():
    """`dbt_models` pins the target path that `pipeline_runs` reads.

    Asserts the *call site*: a real invocation given an explicit path stays green
    when the argument is dropped.
    """
    from pathlib import Path
    from typing import Any

    from modern_data_stack.paths import dbt_run_results_path, dbt_target_path
    from orchestration import assets

    called_with: list[str] = []
    kwargs_seen: dict[str, Any] = {}

    class _Invocation:
        def stream(self):
            return iter(())

    class _Dbt:
        def cli(self, args, **kwargs):
            called_with.extend(args)
            kwargs_seen.update(kwargs)
            return _Invocation()

    # `decorated_fn` is the undecorated generator, so this exercises the call
    # site with no execution harness — nothing materializes or yields, which is
    # the part under test. The annotation states that gap rather than a `ty: ignore`.
    compute: Any = assets.dbt_models.op.compute_fn
    list(compute.decorated_fn(context=None, dbt=_Dbt()))

    assert called_with == ["build"]
    target = kwargs_seen.get("target_path")
    assert target is not None and Path(target) == Path(dbt_target_path()), (
        f"`dbt_models` passes target_path={target!r}. Unpinned, dagster-dbt writes "
        "each invocation's artifacts to a unique directory, and `build_runs` reads "
        "`run_results.json` by path, so every orchestrated build appends nothing to "
        "`analytics.pipeline_runs`."
    )
    # What matters: the two paths agree — the artifact the build writes is the
    # one pipeline_status reads.
    assert Path(target) / "run_results.json" == Path(dbt_run_results_path())
