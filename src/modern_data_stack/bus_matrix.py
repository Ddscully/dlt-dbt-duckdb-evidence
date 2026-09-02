"""Derive Kimball's bus matrix from a dbt manifest.

The bus matrix is the central planning artifact of dimensional design: business
processes down the side, conformed dimensions across the top, a mark where a
process carries a dimension's key. Its value is not the marks but the **holes** —
a fact that cannot be joined to a dimension every other fact shares is either a
deliberate boundary or a defect, and the matrix is what forces someone to say
which.

This project already declares ownership (`_groups.yml`), consumers
(`_exposures.yml`) and shape (contracts). None of the three states which
dimensions a fact conforms to, which is why two structural findings — retail
keyed on its own country labels rather than ISO3, and no published country
dimension at all — took a survey to notice rather than a glance.

**It is derived, never written.** A hand-maintained matrix is one more list that
goes quiet, which this repo has been bitten by often enough to have a name for
(see the `repo-guards` skill). Everything needed is already in the manifest: the
grain comes from each model's own uniqueness tests, and the columns from the
enforced contracts.

Two rules decide what the derivation trusts, and both were learned from this
warehouse:

- **A uniqueness test carrying a `where` is not a grain.**
  `dim_grid_emission_factors` asserts one row per `country_iso3` *where
  `is_latest_available`* — a conditional statement about a slice. Read as a
  grain it makes a country-year reference table look like a conformed country
  dimension, and every fact in the warehouse would appear to conform to it.
- **Conformance is exact column-name matching, deliberately.** Allowing declared
  aliases would hide the one defect the matrix exists to expose: the three FX
  facts reach `dim_currency` under two different names, and an alias list would
  render that as a tidy row of marks. A hole here is a question, not a bug in
  the derivation.

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
    # Dimension-named models with no single-column grain. They are not conformed
    # dimensions and are reported rather than dropped: `dim_country_year` is a
    # coverage table at `(country_iso3, year)` and `dim_grid_emission_factors` a
    # reference product at the same grain. Silently omitting them would make the
    # matrix look complete while two `dim_*` models were missing from it.
    unconformed: tuple[Dimension, ...]
    # Models in the schema whose name matches neither prefix. Classification is
    # by name because the naming convention here is real, but a name outside it
    # must not resolve to *nothing*: such a model would appear in no row and no
    # column, and every assertion about the matrix would still pass. Carried out
    # so a caller can fail on it rather than trusting the convention it cannot
    # see. Empty in this warehouse today, which is the point of measuring it.
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

    Both spellings count. `unique_combination_of_columns` states a compound grain
    and a bare `unique` on a column states a single-column one; a model may carry
    each, and `dim_retail_customer` carries both for the same column.

    Tests narrowed by `where` are skipped — see the module docstring for the one
    that makes this load-bearing rather than tidy.
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

    `alias` rather than `name`, because a versioned model's name is shared by
    every version while the alias is what the release, the Evidence sources and
    the asset keys all spell — `fct_emissions_energy` for v2 and
    `fct_emissions_energy_v1` for v1. Keying on `name` would collapse the two
    into one row of a matrix that is about published relations.
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

    Columns come from the manifest's own `columns` block, which on a contracted
    layer is the enforced list rather than whatever the ymls happened to
    document. On an uncontracted one it is only what someone wrote down, so a
    missing mark there means a missing description — worth knowing before
    pointing this at `staging`.
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

    The cell is the marker, not the key that matched, because a column showing
    two different key names reads as a defect when it is `dim_date` doing exactly
    what a surrogate key is for. `key_notes` reports the multi-key dimensions
    underneath instead, where there is room to say why.
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
    # Loud rather than omitted: a model the matrix could not classify is one it
    # is silently not describing, which is worse than a hole.
    notes += [
        f"**`{model}` is in this schema and is neither a dimension nor a fact by name**, "
        "so no row or column above describes it"
        for model in matrix.unclassified
    ]
    return notes
