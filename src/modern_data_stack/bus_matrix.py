"""Derive Kimball's bus matrix from a dbt manifest.

The bus matrix is the central planning artifact of dimensional design: business
processes down the side, conformed dimensions across the top, a mark where a
process carries a dimension's key. Its value is not the marks but the **holes** —
a fact that cannot be joined to a dimension every other fact shares is either a
deliberate boundary or a defect, and the matrix is what forces someone to say
which.

Groups, exposures and contracts declare ownership, consumers and shape; none
says which dimensions a fact conforms to. This derives that from the manifest
rather than keeping a hand-written list: grains from each model's uniqueness
tests, columns from the enforced contracts.

Two rules decide what the derivation trusts:

- **A uniqueness test carrying a `where` is not a grain.**
  `dim_grid_emission_factors` asserts one row per `country_iso3` only where
  `is_latest_available`; read as a grain, it would look like a conformed country
  dimension every fact conforms to.
- **Conformance is exact column-name matching.** An alias list would render a
  key spelled differently as a mark, hiding the defect the matrix exists to
  expose. A hole is a question, not a bug in the derivation.

Nothing in this module knows what a country is. The schema and the naming
prefixes arrive as arguments; see the project entry point for this warehouse's.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Dimension:
    """A conformed dimension: a dimension model with a single-column grain.

    `keys` holds every such column, because a dimension may publish more than one
    and a fact conforms through any of them — `dim_date` carries the natural
    `date_day` and the `yyyymmdd` surrogate `date_key`, and facts here use both.
    """

    model: str
    keys: tuple[str, ...]


@dataclass(frozen=True)
class Fact:
    """A business process, with the grain it declares and the columns it ships."""

    model: str
    grain: tuple[str, ...]
    columns: frozenset[str]


@dataclass(frozen=True)
class BusMatrix:
    dimensions: tuple[Dimension, ...]
    facts: tuple[Fact, ...]
    # Dimension-named models with no single-column grain (`dim_country_year` is
    # at `(country_iso3, year)`). Reported rather than dropped, so the matrix
    # cannot look complete while a `dim_*` model is missing from it.
    unconformed: tuple[Dimension, ...]
    # Models matching neither prefix. They would otherwise appear in no row or
    # column, so they are carried out for a caller to fail on.
    unclassified: tuple[str, ...]

    def conforms(self, fact: Fact, dimension: Dimension) -> str | None:
        """The dimension key `fact` carries, or None if it carries none.

        Returns the *column name* rather than a boolean so a caller can show
        which key was matched — `dim_date` is reached by two.
        """
        for key in dimension.keys:
            if key in fact.columns:
                return key
        return None


def declared_grains(nodes: dict) -> dict[str, set[tuple[str, ...]]]:
    """Node id -> every unfiltered uniqueness assertion on it, as column tuples.

    Both spellings count: `unique_combination_of_columns` (a compound grain) and
    a column's `unique` (a single-column one). Tests narrowed by `where` are
    skipped — see the module docstring.
    """
    grains: dict[str, set[tuple[str, ...]]] = {}
    for node in nodes.values():
        if node.get("resource_type") != "test":
            continue
        attached = node.get("attached_node")
        if not attached:
            continue
        if (node.get("config") or {}).get("where"):
            continue
        metadata = node.get("test_metadata") or {}
        kwargs = metadata.get("kwargs") or {}
        if metadata.get("name") == "unique_combination_of_columns":
            columns = tuple(kwargs.get("combination_of_columns") or ())
        elif metadata.get("name") == "unique" and node.get("column_name"):
            columns = (node["column_name"],)
        else:
            continue
        if columns:
            grains.setdefault(attached, set()).add(columns)
    return grains


def _relation_name(node: dict) -> str:
    """What the relation is called in the warehouse.

    `alias`, not `name`: every version of a model shares its name, so keying on
    it would collapse `fct_emissions_energy` and `fct_emissions_energy_v1`.
    """
    return node.get("alias") or node["name"]


def build(
    manifest_path: str | Path,
    *,
    schema: str,
    dimension_prefix: str = "dim_",
    fact_prefix: str = "fct_",
) -> BusMatrix:
    """Read `manifest.json` and derive the matrix for one schema.

    Columns come from the manifest's `columns` block: the enforced list on a
    contracted layer, but only what the ymls document on an uncontracted one
    (such as `staging`), where a missing mark may be a missing description.
    """
    manifest = json.loads(Path(manifest_path).read_text())
    nodes = manifest.get("nodes", {})
    grains = declared_grains(nodes)

    models = [
        node
        for node in nodes.values()
        if node.get("resource_type") == "model" and node.get("schema") == schema
    ]

    dimensions: list[Dimension] = []
    unconformed: list[Dimension] = []
    for node in models:
        if not _relation_name(node).startswith(dimension_prefix):
            continue
        singles = sorted(
            {columns[0] for columns in grains.get(node["unique_id"], set()) if len(columns) == 1}
        )
        target = dimensions if singles else unconformed
        target.append(Dimension(model=_relation_name(node), keys=tuple(singles)))

    facts: list[Fact] = []
    unclassified: list[str] = []
    for node in models:
        relation = _relation_name(node)
        if not relation.startswith(fact_prefix):
            if not relation.startswith(dimension_prefix):
                unclassified.append(relation)
            continue
        # The longest declared grain, so a model carrying both a compound grain
        # and an incidental single-column `unique` is described by the compound
        # one. Ties are broken alphabetically to keep the output reproducible.
        candidates = sorted(grains.get(node["unique_id"], set()), key=lambda c: (-len(c), c))
        facts.append(
            Fact(
                model=relation,
                grain=candidates[0] if candidates else (),
                columns=frozenset(node.get("columns", {})),
            )
        )

    return BusMatrix(
        dimensions=tuple(sorted(dimensions, key=lambda d: d.model)),
        facts=tuple(sorted(facts, key=lambda f: f.model)),
        unconformed=tuple(sorted(unconformed, key=lambda d: d.model)),
        unclassified=tuple(sorted(unclassified)),
    )


def to_markdown(matrix: BusMatrix, *, matched: str = "✅", missing: str = "·") -> str:
    """Render the matrix as a GitHub-flavoured markdown table.

    Cells show a marker, not the matched key: a column showing two key names
    would read as a defect when it is `dim_date`'s natural and surrogate keys.
    `key_notes` lists the multi-key dimensions.
    """
    header = ["Business process (fact)", "Grain", *(d.model for d in matrix.dimensions)]
    rows = [
        [
            f"`{fact.model}`",
            "`" + ", ".join(fact.grain) + "`" if fact.grain else "—",
            *(
                matched if matrix.conforms(fact, dimension) else missing
                for dimension in matrix.dimensions
            ),
        ]
        for fact in matrix.facts
    ]
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join(["---"] * len(header)) + "|",
        *("| " + " | ".join(row) + " |" for row in rows),
    ]
    return "\n".join(lines)


def key_notes(matrix: BusMatrix) -> list[str]:
    """One line per dimension reached by more than one key, and per unconformed model."""
    notes = [
        f"`{d.model}` publishes {len(d.keys)} keys: " + ", ".join(f"`{k}`" for k in d.keys)
        for d in matrix.dimensions
        if len(d.keys) > 1
    ]
    notes += [
        f"`{d.model}` is not a conformed dimension: it declares no single-column grain"
        for d in matrix.unconformed
    ]
    # Loud: an unclassified model is one the matrix silently does not describe.
    notes += [
        f"**`{model}` is in this schema and is neither a dimension nor a fact by name**, "
        "so no row or column above describes it"
        for model in matrix.unclassified
    ]
    return notes
