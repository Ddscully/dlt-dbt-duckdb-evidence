"""The bus matrix is derived, and these are what make the derivation trustworthy.

The matrix in `docs/WAREHOUSE.md` says which conformed dimensions each fact
carries. Nothing in dbt can check that: a fact keyed on a column no dimension
publishes builds green, passes its contract and passes its grain test, because
every guard in this repo is scoped to a single relation. That blind spot is what
let retail sit beside the country domain for months without a joinable key.

So the derivation has to be right about two things that are easy to get wrong,
and both are pinned below with the real model that would break them:

- a uniqueness test carrying a `where` is a conditional assertion, not a grain
- a versioned model is two published relations, not one

The third test is the matrix itself: the facts that join to nothing are declared
in `publish/bus_matrix.py` with a reason each, and compared both ways — so a new
orphan fails, and so does fixing one without deleting its entry.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from modern_data_stack import bus_matrix
from modern_data_stack.paths import dbt_manifest_path
from publish.bus_matrix import KNOWN_UNCONFORMED, SCHEMA

# Same reason as `tests/test_additivity.py`: `just test` runs before
# `dbt deps && dbt parse` in ci.yml, so the manifest is not there yet. ci.yml
# re-runs this file after the parse step, and `tests/test_workflows.py` is what
# holds it to that — a gated file the workflow does not name runs nowhere.
manifest_path = dbt_manifest_path()
pytestmark = pytest.mark.skipif(
    not Path(manifest_path).exists(),
    reason="needs dbt/target/manifest.json — run `just dbt-deps` and `dbt parse` first",
)


@pytest.fixture(scope="module")
def matrix() -> bus_matrix.BusMatrix:
    return bus_matrix.build(manifest_path, schema=SCHEMA)


def test_a_filtered_uniqueness_test_is_not_read_as_a_grain(matrix):
    """`dim_grid_emission_factors` is the model that makes this load-bearing.

    It asserts one row per `country_iso3` **where `is_latest_available`** — true
    of a slice, and false of the table, which is a country-year reference product.
    Read as a grain it becomes a conformed country dimension, and since every
    country fact carries `country_iso3`, the matrix fills with marks that mean
    nothing. Mutation-checked: dropping the `where` filter from `declared_grains`
    moves it into `dimensions` and adds a fully-populated column.
    """
    assert "dim_grid_emission_factors" in {d.model for d in matrix.unconformed}
    assert "dim_grid_emission_factors" not in {d.model for d in matrix.dimensions}

    # and the filtered test really is the only thing standing between the two
    nodes = json.loads(Path(manifest_path).read_text())["nodes"]
    filtered = [
        node
        for node in nodes.values()
        if node.get("resource_type") == "test"
        and (node.get("config") or {}).get("where")
        and "grid_emission_factors" in (node.get("attached_node") or "")
    ]
    assert filtered, "the where-filtered grain test this guard is about has gone"


def test_a_versioned_model_is_one_row_per_published_relation(matrix):
    """`fct_emissions_energy` is two relations: v2 aliased bare, and the v1 view.

    Keying the matrix on `name` collapses them, which would under-report the
    published layer by exactly the relation a consumer is most likely to still be
    reading — the compatibility view inside its deprecation window.
    """
    facts = {f.model for f in matrix.facts}
    assert {"fct_emissions_energy", "fct_emissions_energy_v1"} <= facts


def test_the_facts_that_join_to_nothing_are_exactly_the_declared_ones(matrix):
    """Both directions, so the declaration cannot go quiet in either.

    A fact conforming to no dimension is not automatically wrong — an aggregate
    that has dimensioned its keys away is a real thing — but it is always a
    decision, and this is what makes someone write it down.
    """
    orphans = {
        fact.model
        for fact in matrix.facts
        if not any(matrix.conforms(fact, dim) for dim in matrix.dimensions)
    }
    assert orphans == set(KNOWN_UNCONFORMED), (
        "the facts conforming to no dimension have changed.\n"
        f"  undeclared orphans (add them to KNOWN_UNCONFORMED with a reason): "
        f"{sorted(orphans - set(KNOWN_UNCONFORMED))}\n"
        f"  declared but now conformed (delete the entry): "
        f"{sorted(set(KNOWN_UNCONFORMED) - orphans)}"
    )


def test_every_conformed_dimension_is_used_by_at_least_one_fact(matrix):
    """The mirror of the test above, and it catches the opposite failure.

    A dimension no fact carries the key of is either unpublished work or dead
    weight in a release consumers pay to download. `dim_country` spent months as
    the first of those — promised by the `reference` group's own description and
    not built.
    """
    unused = [
        dim.model
        for dim in matrix.dimensions
        if not any(matrix.conforms(fact, dim) for fact in matrix.facts)
    ]
    assert not unused, f"conformed dimensions no fact joins to: {unused}"


def test_the_block_in_the_doc_is_what_the_manifest_produces_today():
    """The rendered table must match the manifest, or the doc is just a picture.

    This is the half that makes the matrix *derived* rather than merely
    generated-once. Without it, adding a mart and not running `just bus-matrix`
    leaves a table that is confidently wrong and reads as authoritative — the
    exact failure the counts guard exists for, one artifact along.
    """
    from modern_data_stack.paths import project_root
    from publish.bus_matrix import DOC_PATH, MARKER_BEGIN, MARKER_END, render

    text = (project_root() / DOC_PATH).read_text()
    start, end = text.find(MARKER_BEGIN), text.find(MARKER_END)
    assert start != -1 and end != -1, f"{DOC_PATH} has lost its bus-matrix markers"
    assert text[start : end + len(MARKER_END)] == render(), (
        f"{DOC_PATH}'s bus matrix is stale — run `just bus-matrix`"
    )


def test_a_mart_that_is_neither_a_dimension_nor_a_fact_is_reported_not_dropped(matrix):
    """The vacuity guard, and the only one here that asserts against a mutation.

    Classification is by name, which is honest — the convention is real. What is
    not safe is a name outside it resolving to *nothing*: such a model appears in
    no row and no column, and every other assertion in this file still passes,
    because they all quantify over what the matrix already contains.

    So this checks both halves. The tree is clean today, and a synthetic mart
    that breaks the convention has to come back in `unclassified` rather than
    vanish — without the second half, deleting the tracking entirely would leave
    this test green.
    """
    assert matrix.unclassified == (), (
        "marts that are neither `dim_` nor `fct_` by name, so the matrix does not "
        f"describe them: {list(matrix.unclassified)}"
    )

    manifest = json.loads(Path(manifest_path).read_text())
    manifest["nodes"]["model.test.bridge_country_region"] = {
        "resource_type": "model",
        "schema": SCHEMA,
        "name": "bridge_country_region",
        "alias": "bridge_country_region",
        "unique_id": "model.test.bridge_country_region",
        "columns": {"country_iso3": {}, "region_key": {}},
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(manifest, handle)
        injected = handle.name
    try:
        built = bus_matrix.build(injected, schema=SCHEMA)
    finally:
        Path(injected).unlink()

    assert built.unclassified == ("bridge_country_region",)
    assert any("neither a dimension nor a fact" in note for note in bus_matrix.key_notes(built))
