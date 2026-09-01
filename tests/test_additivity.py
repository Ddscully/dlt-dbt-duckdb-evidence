"""Which measures may be summed, declared on the column and held to the models.

A Parquet file carries column names and types and nothing else. Nothing in it
says that `co2_mt` may be summed while `renewables_share_pct` may not, or that
`population` adds across countries and not across years — and 92 of the 188
numeric mart columns are in the first of those categories, 13 in the second. So
the warehouse states it: `meta: {additivity: …}` on the column, in the same ymls
that carry the contract, and `publish/export_warehouse.py` carries the labels
into the release manifest so a consumer who cannot be paged has them too.

The vocabulary is four values and closed, for `pii`'s reason exactly — a blank
is ambiguous between "additive" and "nobody looked", and only one of those can
be true by accident:

* `additive` — a sum along every dimension of the table is the same kind of
  thing. Extensive quantities: money, tonnes, counted rows, durations.
* `semi_additive` — summable in some directions and not others. **The
  description has to say which**, which is what `test_a_semi_additive_column_says_which_direction_fails`
  requires: the label without the direction is decoration.
* `non_additive` — never summable. Ratios, rates, prices, averages, extrema and
  distinct counts. Aggregating one means recomputing it from its components.
* `not_a_measure` — a key, a calendar part, or a parameter carried on the row
  (`heating_base_c`, `ets_price_eur_per_t`). Stated rather than left blank, for
  the reason `non_personal` is.

The intensive/extensive distinction is what separates the first two from the
third and it is not a matter of taste: `days_to_return` summed over returns is
total shelf-days, a real quantity, so it is additive; `temp_mean_c` summed over
countries is nothing at all.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from modern_data_stack.paths import dbt_manifest_path
from publish.export_warehouse import additivity

# Same reason as `tests/test_definitions.py`: `just test` runs before
# `dbt deps && dbt parse` in ci.yml, so the manifest is not there yet. ci.yml
# re-runs this file after the parse step, and `tests/test_workflows.py` is what
# holds it to that — a gated file the workflow does not name runs nowhere.
manifest_path = dbt_manifest_path()
pytestmark = pytest.mark.skipif(
    not Path(manifest_path).exists(),
    reason="needs dbt/target/manifest.json — run `just dbt-deps` and `dbt parse` first",
)

LABELS = {"additive", "semi_additive", "non_additive", "not_a_measure"}

# The contract gives every mart column a `data_type`, so "is this a measure-
# shaped column" is answerable without opening the warehouse.
NUMERIC = {"DOUBLE", "BIGINT", "INTEGER", "FLOAT", "HUGEINT", "SMALLINT", "TINYINT"}

# Names that promise a ratio. Deliberately a *name* rule and not a type rule:
# it is the only thing here that can catch a label which is present and wrong.
RATIO_SHAPED = re.compile(r"(_pct$|_per_|_share|share_|_rate$|rate_|intensity|median_|avg_|price)")


def mart_columns() -> dict[tuple[str, str], dict]:
    """Every column of every marts model, keyed by (relation, column).

    Keyed on the *published* relation (`schema.alias`) rather than the model
    name, because the versioned model is two relations and they are labelled
    independently — v1 inherits its 36 through `include: all` and declares
    `co2_per_gdp` itself.
    """
    manifest = json.loads(Path(manifest_path).read_text())
    out = {}
    for node in manifest["nodes"].values():
        if node.get("resource_type") != "model" or not node.get("path", "").startswith("marts/"):
            continue
        relation = f"{node['schema']}.{node.get('alias') or node['name']}"
        for column, spec in (node.get("columns") or {}).items():
            out[(relation, column)] = spec
    return out


def labelled() -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for key, spec in mart_columns().items():
        label = (spec.get("meta") or {}).get("additivity")
        if label:
            out[key] = label
    return out


def numeric() -> dict[tuple[str, str], dict]:
    return {
        key: spec
        for key, spec in mart_columns().items()
        if (spec.get("data_type") or "").upper() in NUMERIC
    }


def test_every_numeric_mart_column_carries_an_additivity_label():
    """The exhaustive half. A new measure with no label is the failure mode.

    Scoped to `marts` on purpose, and the boundary is a decision rather than
    where the work stopped: marts are the published interface and the layer
    where "measure" means something. `staging` is a cleaning copy of a source
    whose measures are declared one layer up, and `analytics` is written by
    Polars and invisible to dbt — the same gap `EXTRA_CLASSIFICATIONS` fills for
    `pii`, and it is open here.
    """
    missing = sorted(key for key in numeric() if key not in labelled())
    assert not missing, (
        f"{len(missing)} numeric mart columns carry no `meta: {{additivity: …}}`: {missing}"
    )


def test_the_label_vocabulary_is_closed():
    """Four labels, and a typo is a fifth. Same rule as the `pii` vocabulary:
    a label nobody chose deliberately is how a classification becomes
    decoration."""
    assert set(labelled().values()) == LABELS


def test_only_numeric_columns_are_labelled():
    """The vacuity guard. Every assertion above is over the numeric columns, so
    a rule that labelled everything — or that drifted onto the varchars — would
    satisfy them all while meaning nothing."""
    stray = sorted(key for key in labelled() if key not in numeric())
    assert not stray, f"labelled but not a numeric column: {stray}"


def test_a_ratio_shaped_name_is_never_summable():
    """The half that can catch a label which is present and *wrong*.

    Coverage alone cannot: a column labelled `additive` that is really a
    percentage is as labelled as one that isn't. A name carrying `_pct`,
    `_per_`, `share`, `rate`, `intensity`, `median_`, `avg_` or `price` is a
    ratio by construction, so `additive` or `semi_additive` on one is a
    contradiction between the name and the label — and it holds across the whole
    tree today with **no exceptions**, which is what makes it worth asserting
    rather than documenting.

    One-directional on purpose. Plenty of non-additive columns are not
    ratio-named (`temp_mean_c`, `longest_gap_days`, `n_customers`), and
    requiring the converse would be asserting that this pattern is a complete
    theory of measures, which it isn't.
    """
    summable = sorted(
        (relation, column, label)
        for (relation, column), label in labelled().items()
        if RATIO_SHAPED.search(column) and label in {"additive", "semi_additive"}
    )
    assert not summable, f"named like a ratio but declared summable: {summable}"


def test_a_semi_additive_column_says_which_direction_fails():
    """`semi_additive` is the only label that is useless on its own.

    "Summable in some directions" without saying which leaves a reader exactly
    where they started. `cohort_size` adds across cohorts and multiplies down
    one; `population` adds across countries and gives person-years across years;
    `original_quantity` belongs to the purchase, and 16,398 matched returns point
    at 15,312 distinct purchases, so summing it counts 1,086 of them twice. None
    of that is recoverable from the label.
    """
    silent = sorted(
        key
        for key, label in labelled().items()
        if label == "semi_additive" and not (mart_columns()[key].get("description") or "").strip()
    )
    assert not silent, f"semi_additive with no description saying which direction fails: {silent}"


def test_the_labels_reach_the_release_manifest():
    """A label with no consequence is decoration — the same argument that makes
    `direct_identifier` real is what puts these in `manifest.json`.

    The release is the audience that cannot ask: a Parquet consumer has the
    types and nothing else. Asserted against the ymls rather than against a
    frozen number, so the two cannot drift apart.
    """
    shipped = additivity()
    assert shipped is not None, "the dbt manifest exists, so the export must have read it"
    flat = {
        (relation, column): label
        for relation, columns in shipped.items()
        for column, label in columns.items()
    }
    assert flat == labelled()
