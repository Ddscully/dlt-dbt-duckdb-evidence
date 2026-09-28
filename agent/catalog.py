"""What a table means, read from the dbt manifest: its grain, its columns, which may be summed.

A model asked about revenue has to know that `unit_price` is not a thing to add
up, that `fct_retail_order_line` has one row per invoice line, and that
`customer_id` is personal data. All of that is already written down, once, in
the marts ymls: the grain as a uniqueness test, each column's `additivity`
label, its `pii` class and its description. `dbt parse` puts them in
`manifest.json`, and the same text ships in the release's catalogue, so this
module reads the manifest and adds nothing of its own.

The output is short on purpose. A small model's context holds a few thousand
words, and `fct_retail_order_line` has dozens of columns, so each column gets one
line: its type, its label and the first sentence of its description.

The tables described are the `retail` group's marts and every model a semantic
model reads, which is what `query_metric` can reach.

Run:  uv run python -m agent.catalog fct_retail_order_line
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

from agent.metrics import GROUP_BY, TIME_GRAINS, SemanticLayer, load_semantic_layer
from modern_data_stack.bus_matrix import declared_grains
from modern_data_stack.paths import dbt_manifest_path


@dataclass(frozen=True)
class Catalog:
    """Everything the describe_model and query_metric tools read, loaded once."""

    manifest: dict  # manifest.json, as parsed
    layer: SemanticLayer

    @property
    def describable(self) -> dict[str, dict]:
        return describable(self.manifest)


def load_manifest(path: str | Path | None = None) -> dict:
    path = Path(path or dbt_manifest_path())
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist: run `just dbt-parse`")
    return json.loads(path.read_text())


def load_catalog() -> Catalog:
    return Catalog(load_manifest(), load_semantic_layer())


def describable(manifest: dict) -> dict[str, dict]:
    """Model name -> node, for the retail marts and every model a semantic model reads."""
    read = {
        node_id
        for semantic_model in manifest.get("semantic_models", {}).values()
        for node_id in semantic_model["depends_on"]["nodes"]
    }
    out = {}
    for node_id, node in manifest["nodes"].items():
        if node.get("resource_type") != "model" or node.get("schema") != "marts":
            continue
        # A versioned relation (`alias` differs from `name`) is a compatibility view.
        if (node.get("alias") or node["name"]) != node["name"]:
            continue
        if node.get("group") == "retail" or node_id in read:
            out[node["name"]] = node
    return dict(sorted(out.items()))


def _first(text: str, *, paragraph: bool = False) -> str:
    """The first paragraph, or sentence, of a description, on one line.

    dbt keeps a `>` description's paragraph breaks as single newlines, and a
    `|` one's as blank lines. A full stop inside backticks or brackets
    (`St. Martin (French part)`) does not end a sentence.
    """
    text = (text or "").strip()
    text = text.split("\n\n" if "\n\n" in text else "\n")[0]
    text = re.sub(r"\s+", " ", text)
    if paragraph:
        return text
    depth, code = 0, False
    for i, char in enumerate(text):
        if char == "`":
            code = not code
        elif not code and char in "([":
            depth += 1
        elif not code and char in ")]":
            depth = max(depth - 1, 0)
        elif char == "." and not code and depth == 0 and text[i + 1 : i + 2] in (" ", ""):
            return text[: i + 1]
    return text


def _metrics_reading(layer: SemanticLayer, alias: str) -> dict[str, set[str]]:
    """Metric -> the columns of `alias` its measures read."""
    out: dict[str, set[str]] = {}
    for metric in layer.metrics:
        for model, measure in layer.measures(metric):
            if model["node_relation"]["alias"] == alias:
                out.setdefault(metric, set()).add(measure.get("expr") or measure["name"])
    return out


def _grouped_by(layer: SemanticLayer, alias: str) -> list[str]:
    """The `query_metric` groupings this model supplies."""
    names = []
    for key, ref in GROUP_BY.items():
        entity, dimension = ref.split("__", 1)
        for model in layer.manifest["semantic_models"]:
            if model["node_relation"]["alias"] != alias:
                continue
            if key in TIME_GRAINS:
                supplies = bool(model["measures"]) and bool(model.get("defaults"))
            else:
                primary = {e["name"] for e in model["entities"] if e["type"] == "primary"}
                supplies = entity in primary and any(
                    d["name"] == dimension for d in model["dimensions"]
                )
            if supplies:
                names.append(key)
    return names


def describe_model(catalog: Catalog, name: str) -> str:
    """One model, as a model should read it before choosing a metric or a column."""
    models = catalog.describable
    if name not in models:
        raise ValueError(f"no model {name!r} to describe; the models are {', '.join(models)}")
    node = models[name]
    alias = node.get("alias") or name
    grains = declared_grains(catalog.manifest["nodes"]).get(node["unique_id"], set())
    # Several unique keys: the narrowest, and among those the one declared first.
    order = list(node.get("columns", {}))
    grain = (
        min(
            grains,
            key=lambda g: (len(g), [order.index(c) if c in order else len(order) for c in g]),
        )
        if grains
        else None
    )

    lines = [f"marts.{alias}"]
    lines.append(f"One row per ({', '.join(grain)})." if grain else "No declared grain.")
    lines.append(_first(node.get("description", ""), paragraph=True))
    reading = _metrics_reading(catalog.layer, alias)
    if reading:
        lines.append(f"Metrics for query_metric: {', '.join(sorted(reading))}.")
    grouped = _grouped_by(catalog.layer, alias)
    if grouped:
        lines.append(f"query_metric groups by it as: {', '.join(grouped)}.")
    lines.append("Columns (name, type, additivity: whether a sum of it means anything):")
    for column, spec in node.get("columns", {}).items():
        meta = spec.get("meta") or {}
        label = meta.get("additivity") or "—"
        if meta.get("pii") == "direct_identifier":
            counted = [metric for metric, columns in reading.items() if column in columns]
            label = "personal data: never grouped by or listed"
            if counted:
                label += f", counted by {', '.join(counted)}"
        data_type = spec.get("data_type") or "?"
        lines.append(f"- {column} ({data_type}; {label}): {_first(spec.get('description', ''))}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("model")
    args = parser.parse_args()
    catalog = load_catalog()
    try:
        print(describe_model(catalog, args.model))
    except ValueError as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
