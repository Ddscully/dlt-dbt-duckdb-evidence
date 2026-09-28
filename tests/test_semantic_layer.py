"""The metrics in dbt's semantic layer are held to the columns' labels.

MetricFlow sums whatever a measure tells it to. It does not read the
`additivity` labels: a measure summing `unit_price` compiles and returns a
figure, and a dimension on `customer_id` would let a model list customers. So
the rules the labels imply are enforced here, over `manifest.json`, where each
semantic model sits beside the model node it reads:

* every measure is a bare column, so its label can be looked up — a subset is
  chosen with a metric's `filter:`;
* a measure sums only a column labelled `additive`;
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


@pytest.fixture(scope="module")
def semantic_models() -> list[tuple[str, dict, dict]]:
    """(name, semantic model, the model node it reads), from manifest.json."""
    manifest = json.loads(Path(manifest_path).read_text())
    out = []
    for semantic_model in manifest["semantic_models"].values():
        (node_id,) = semantic_model["depends_on"]["nodes"]
        out.append((semantic_model["name"], semantic_model, manifest["nodes"][node_id]))
    assert out, "no semantic models in the manifest"
    return out


def test_every_measure_is_a_column_and_sums_only_an_additive_one(semantic_models):
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
            if measure["agg"] == "sum" and label != "additive":
                problems.append(f"{where} sums {column}, which is labelled {label}")
    assert not problems, "\n".join(problems)


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
