"""The metrics in dbt's semantic layer are held to the columns' labels and tests.

MetricFlow sums whatever a measure tells it to. It does not read the
`additivity` labels: a measure summing `unit_price` compiles and returns a
figure, and a dimension on `customer_id` would let a model list customers. So
the rules the labels imply are enforced here, over `manifest.json`, where each
semantic model sits beside the model node it reads:

* every measure is a bare column, so its label can be looked up — a subset is
  chosen with a metric's `filter:`;
* a measure sums only a column labelled `additive`, or one labelled
  `semi_additive` with a `non_additive_dimension` on its time dimension: a
  population is summed across countries, never across years;
* `fill_nulls_with` reads only a column a `not_null` test holds, where a null can
  only mean "no rows". Over a country-year column a null is a year nobody
  reported: filled, 2025 read as 0.0 Mt of CO2;
* an input of a ratio or derived metric that reads a column that can be null
  carries a filter, so both sides count the same rows: an unmatched CO2 per
  person counted Taiwan's emissions and not its population. The test sees that
  a filter is there, not that it is the right one; that is review's job;
* each name `query_metric` offers (`config.meta.offered_as`) is one categorical
  dimension, and not a time grain's name;
* no entity or dimension reads a column classified `direct_identifier`.

And every metric compiles, grouped by year and by region, because
`agent/metrics.py` drives MetricFlow's engine, which is not a documented API and
moves with dbt-core: an upgrade that breaks it fails here, not in an answer.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from modern_data_stack.paths import dbt_manifest_path, dbt_semantic_manifest_path

# ci.yml runs pytest before `dbt parse`, so the manifests are missing there; it
# re-runs this file after the parse, which `tests/test_workflows.py` enforces.
manifest_path = dbt_manifest_path()
semantic_manifest_path = dbt_semantic_manifest_path()
pytestmark = pytest.mark.skipif(
    not (Path(manifest_path).exists() and Path(semantic_manifest_path).exists()),
    reason="needs dbt/target/manifest.json — run `just dbt-deps` and `dbt parse` first",
)

# Aggregations that return a number over rows whose column is all null: a count
# of nothing is 0, never null, so filling or filtering does not change it.
COUNTS = ("count", "count_distinct")


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(Path(manifest_path).read_text())


@pytest.fixture(scope="module")
def semantic_models(manifest) -> list[tuple[str, dict, dict]]:
    """(name, semantic model, the model node it reads), from manifest.json."""
    out = []
    for semantic_model in manifest["semantic_models"].values():
        (node_id,) = semantic_model["depends_on"]["nodes"]
        out.append((semantic_model["name"], semantic_model, manifest["nodes"][node_id]))
    assert out, "no semantic models in the manifest"
    return out


def _not_null_columns(manifest: dict, node_id: str) -> set[str]:
    """The columns of a model that a `not_null` data test or constraint holds."""
    tested = {
        test["column_name"]
        for test in manifest["nodes"].values()
        if test["resource_type"] == "test"
        and test.get("attached_node") == node_id
        and (test.get("test_metadata") or {}).get("name") == "not_null"
    }
    constrained = {
        column
        for column, spec in manifest["nodes"][node_id]["columns"].items()
        if any(c.get("type") == "not_null" for c in spec.get("constraints") or [])
    }
    return tested | constrained


def _nullable_columns(manifest: dict, metric: dict) -> list[str]:
    """The columns `metric`'s measures read that can be null, counts aside."""
    measures = {
        measure["name"]: (semantic_model, measure)
        for semantic_model in manifest["semantic_models"].values()
        for measure in semantic_model["measures"]
    }
    out = []
    for ref in metric["type_params"]["input_measures"]:
        semantic_model, measure = measures[ref["name"]]
        if measure["agg"] in COUNTS:
            continue
        (node_id,) = semantic_model["depends_on"]["nodes"]
        column = measure.get("expr") or measure["name"]
        if column not in _not_null_columns(manifest, node_id):
            out.append(column)
    return out


def test_every_measure_is_a_column_and_sums_only_what_its_label_allows(semantic_models):
    problems = []
    for name, semantic_model, node in semantic_models:
        columns = node["columns"]
        for measure in semantic_model["measures"]:
            column = measure.get("expr") or measure["name"]
            where = f"{name}.{measure['name']}"
            if column not in columns:
                problems.append(
                    f"{where} reads {column!r}, not a column: filter with the metric instead"
                )
                continue
            label = (columns[column].get("meta") or {}).get("additivity")
            if measure["agg"] != "sum" or label == "additive":
                continue
            time_dimension = measure.get("agg_time_dimension") or (
                semantic_model.get("defaults") or {}
            ).get("agg_time_dimension")
            not_across = (measure.get("non_additive_dimension") or {}).get("name")
            if label != "semi_additive":
                problems.append(f"{where} sums {column}, which is labelled {label}")
            elif not_across != time_dimension:
                problems.append(
                    f"{where} sums {column}, which is semi_additive: give the measure "
                    f"a non_additive_dimension on {time_dimension}"
                )
    assert not problems, "\n".join(problems)


def test_a_null_is_filled_only_where_the_column_cannot_be_null(manifest):
    problems = []
    for metric in manifest["metrics"].values():
        ref = metric["type_params"].get("measure") or {}
        if ref.get("fill_nulls_with") is None:
            continue
        for column in _nullable_columns(manifest, metric):
            problems.append(
                f"{metric['name']} fills nulls with {ref['fill_nulls_with']}, but {column} "
                "can be null, where a null is unreported, not zero: give the column a "
                "not_null test, or drop fill_nulls_with"
            )
    assert not problems, "\n".join(problems)


def test_a_ratio_counts_the_same_rows_above_and_below_the_line(manifest):
    by_name = {metric["name"]: metric for metric in manifest["metrics"].values()}
    problems = []
    for metric in by_name.values():
        params = metric["type_params"]
        if metric["type"] == "ratio":
            inputs = [params["numerator"], params["denominator"]]
        elif metric["type"] == "derived":
            inputs = params["metrics"]
        else:
            continue
        for ref in inputs:
            if ref.get("filter"):
                continue
            problems += [
                f"{metric['name']} takes {ref['name']} unfiltered, and its column {column} "
                "can be null: filter both inputs to the rows where both are reported"
                for column in _nullable_columns(manifest, by_name[ref["name"]])
            ]
    assert not problems, "\n".join(problems)


def test_each_offered_name_is_one_categorical_dimension(manifest):
    from agent.metrics import TIME_GRAINS

    offered: dict[str, str] = {}
    problems = []
    for semantic_model in manifest["semantic_models"].values():
        for dimension in semantic_model["dimensions"]:
            name = ((dimension.get("config") or {}).get("meta") or {}).get("offered_as")
            if name is None:
                continue
            where = f"{semantic_model['name']}.{dimension['name']}"
            if dimension["type"] != "categorical":
                problems.append(f"{where} offers {name!r}, but only a categorical dimension can")
            if name in TIME_GRAINS:
                problems.append(f"{where} offers {name!r}, which is a time grain's name")
            if name in offered:
                problems.append(f"{where} offers {name!r}, which {offered[name]} offers already")
            offered[name] = where
    assert not problems, "\n".join(problems)
    assert offered, "no dimension is offered to query_metric"


def test_no_entity_or_dimension_reads_a_direct_identifier(semantic_models):
    problems = []
    for name, semantic_model, node in semantic_models:
        identifiers = [
            column
            for column, spec in node["columns"].items()
            if (spec.get("meta") or {}).get("pii") == "direct_identifier"
        ]
        for kind in ("entities", "dimensions"):
            for element in semantic_model[kind]:
                expr = element.get("expr") or element["name"]
                problems += [
                    f"{name}.{element['name']} reads {column}"
                    for column in identifiers
                    if re.search(rf"\b{column}\b", expr)
                ]
    assert not problems, "\n".join(problems)


def test_every_metric_compiles_by_year_and_by_region():
    from metricflow.engine.metricflow_engine import MetricFlowQueryRequest

    from agent.metrics import GROUP_BY, load_semantic_layer

    layer = load_semantic_layer(semantic_manifest_path)
    for group_by in ("year", "region"):
        request = MetricFlowQueryRequest.create(
            metric_names=list(layer.metrics), group_by_names=[GROUP_BY[group_by]]
        )
        sql = layer.engine.explain(request).sql_statement.sql
        assert "fct_retail_order_line" in sql
