"""Every asset and check in `assets.py` is registered in `definitions.py`.

`dg.Definitions` takes an explicit list, and nothing complains about an omission:
the asset simply isn't in the graph, so `AssetSelection.all()` never sees it and
`dagster definitions validate` passes. That is not a hypothetical — the retail
ingest and `analytics.retail_rfm` were added to `assets.py` and never listed, so
`full_refresh` ran without them and `dbt build` failed one layer later with
`Catalog Error: Table with name retail_invoice_lines does not exist!`. The two
asset checks added alongside the currency and retail work were missing the same
way, and those fail *silently* — a check nobody registered just never runs.

The explicit list stays (it is the one place that says what the graph is); this
is what makes forgetting it loud.
"""

from __future__ import annotations

import dagster as dg
import pytest

from orchestration.resources import dbt_project

# `just test` runs before `dbt deps && dbt parse` in ci.yml, and importing
# `orchestration.assets` needs the manifest that parse writes. CI re-runs this
# file after the parse step; skipping keeps the unit-test tier importable in a
# fresh clone rather than failing it for a missing build artifact.
pytestmark = pytest.mark.skipif(
    not dbt_project.manifest_path.exists(),
    reason="needs dbt/target/manifest.json — run `just dbt-deps` and `dbt parse` first",
)

# The dlt-pipeline-deactivation fixture this file needs (importing the
# orchestration layer leaves a dlt pipeline active process-wide) lives in
# `tests/conftest.py`, shared with `test_asset_checks.py`.


def _defined_in_assets_module():
    """(asset keys, check keys) declared at module level in `assets.py`."""
    from orchestration import assets

    asset_keys: set[dg.AssetKey] = set()
    check_keys: set[dg.AssetCheckKey] = set()
    for value in vars(assets).values():
        # `AssetChecksDefinition` is a *subclass* of `AssetsDefinition`, so this
        # order is load-bearing: the other way round every check falls into the
        # first branch, contributes an empty `.keys`, and the check set comes out
        # empty — a test that passes by measuring nothing. Caught by
        # unregistering two checks and watching it stay green.
        if isinstance(value, dg.AssetChecksDefinition):
            check_keys.update(value.check_keys)
        elif isinstance(value, dg.AssetsDefinition):
            asset_keys.update(value.keys)
    return asset_keys, check_keys


def test_every_asset_defined_is_in_the_graph():
    from orchestration.definitions import defs

    defined, _ = _defined_in_assets_module()
    # Executable, not `get_all_asset_keys()`: an unregistered asset that
    # something *depends on* still shows up in the graph as an external node, so
    # the wider set reports `analytics/retail_rfm` present purely because
    # `pipeline_status` names it in `deps`. Verified by unregistering it — the
    # wide assertion passes, this one fails.
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
    """`RAW_DESCRIPTIONS` is the second hand-maintained list re-enumerating the
    seven dlt resources, and the only one that cannot be derived — the prose is
    not computable from the source, so this list stays and gets held to it.

    `assets.py:156` reads it as `RAW_DESCRIPTIONS.get(name)`, which returns
    `None` for an unlisted resource. The asset then materialises with no
    description and nothing anywhere is red: the Dagster UI simply shows a blank
    where every sibling has a sentence, which is the sort of gap that survives
    review indefinitely.

    Lives here rather than in `tests/test_ingest.py` because reading the dict
    means importing `orchestration.assets`, which needs both dagster (an
    optional group) and the dbt manifest. This module already carries the
    manifest skipif and CI re-runs the file after `dbt parse`, so the guard does
    execute in CI; dragging a dagster import into `test_ingest.py` would only
    spread the skip to the one module that runs clean in a fresh clone.
    """
    from ingest import pipeline
    from orchestration.assets import RAW_DESCRIPTIONS

    resources = {r.name for r in pipeline.public_indicators().resources.values()}

    # Both directions, as separate assertions rather than one set equality: they
    # catch different bugs and the failure message should say which happened.
    undescribed = resources - RAW_DESCRIPTIONS.keys()
    assert not undescribed, (
        "dlt resources with no entry in orchestration/assets.py RAW_DESCRIPTIONS "
        f"(they materialise with no description): {sorted(undescribed)}"
    )

    # The reverse direction is the one nothing else could ever surface. `.get()`
    # never consults a key that no resource matches, so a stale entry left by a
    # rename is invisible for as long as it survives — where a *missing* entry at
    # least shows as a blank in the UI next to six siblings that have prose.
    orphaned = RAW_DESCRIPTIONS.keys() - resources
    assert not orphaned, (
        "RAW_DESCRIPTIONS entries naming no dlt resource — renamed or removed "
        f"upstream and left behind here: {sorted(orphaned)}"
    )

    # A key check alone is satisfied by an empty string, which renders as the
    # same blank the missing key does. The floor is deliberately a length rather
    # than truthiness: `" "` is falsy nowhere and blank everywhere.
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

    `full_refresh` excludes the retail *ingest* — it has to, an asset job takes
    one partitions definition — so the exclusion has to be paid for by
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
    a source added to `MONTH_PARTITIONED_RESOURCES` would join that block and be
    dropped from `full_refresh` silently.
    """
    from orchestration import assets

    retail_keys = set(assets.raw_retail_asset.keys)
    assert retail_keys == {dg.AssetKey(["raw", "retail_invoice_lines"])}
    assert not retail_keys & _job_keys("full_refresh")
    assert retail_keys == _job_keys("load_retail")

    # The downstream retail models are unpartitioned and must stay in the graph.
    assert dg.AssetKey(["analytics", "retail_rfm"]) in _job_keys("full_refresh")


def test_every_retail_month_has_a_partition_to_land_in():
    """`TimeWindowPartitionsDefinition`'s `end` is *exclusive*.

    Passing `RETAIL_LAST_MONTH` straight through is the obvious thing to write
    and it drops that month: 24 keys ending at 2011-11, with December 2011's
    25,526 lines unreachable through the partitioned path and no key that could
    ask for them. The unpartitioned path — every workflow, every justfile recipe
    — loads the whole file regardless, so nothing was ever red; a backfill simply
    stopped a month early. This asserts the closed interval the constants
    describe, so the off-by-one cannot come back at either end.
    """
    from ingest.pipeline import RETAIL_FIRST_MONTH, RETAIL_LAST_MONTH
    from orchestration.assets import RETAIL_PARTITIONS

    keys = RETAIL_PARTITIONS.get_partition_keys()
    assert keys[0] == RETAIL_FIRST_MONTH
    assert keys[-1] == RETAIL_LAST_MONTH
    # 2009-12 through 2011-12 inclusive, i.e. no gaps in between either.
    assert len(keys) == 25
