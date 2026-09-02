"""Which measures may be summed, declared on the column and held to the models.

A Parquet file carries column names and types and nothing else. Nothing in it
says that `co2_mt` may be summed while `renewables_share_pct` may not, or that
`population` adds across countries and not across years — and 117 of the 226
numeric mart columns are non-additive, with 16 more semi_additive. So
the warehouse states it: `meta: {additivity: …}` on the column, in the same ymls
that carry the contract, and `publish/export_warehouse.py` carries the labels
into the release manifest so a consumer who cannot be paged has them too.

**Every count in this docstring is a manifest count, which is the basis
`numeric()` below returns and the only one anything here can check.** The ymls
carry 190 literal `additivity:` lines; the manifest carries 226 labelled
columns, because `fct_emissions_energy_v1` inherits 36 through `include: all`
and declares one. Quoting the first while naming the second is how these figures
went stale once already, so
`test_every_documented_additivity_count_is_one_the_labels_actually_carry`
now reads them out of the prose.

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

import datetime as dt
import json
import re
from pathlib import Path

import polars as pl
import pytest

from modern_data_stack.paths import dbt_manifest_path
from publish.export_warehouse import EXTRA_ADDITIVITY, additivity
from transform.retail_rfm import build_retail_rfm

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
#
# **A pattern rather than a list, because a list fails in the wrong direction.**
# The literal set this replaced held seven names and no `DECIMAL` — the most
# natural type for money — so a column contracted `DECIMAL(18,2)` was exempt
# from `test_every_numeric_mart_column_carries_an_additivity_label`, and
# labelling it anyway reddened `test_only_numeric_columns_are_labelled`: doing
# the right thing broke the suite. Five types appear in the contracts today
# (BIGINT, DOUBLE, HUGEINT, INTEGER and VARCHAR/BOOLEAN/DATE/TIMESTAMP), so the
# gap was invisible and would have stayed invisible until the first fixed-point
# column arrived.
#
# The `\b` is load-bearing twice over: `INTERVAL` begins `INT` and is not a
# measure, and `INTEGER[]` is a list rather than something to sum, which is what
# the lookahead excludes. `test_the_numeric_pattern_knows_a_measure_from_a_
# timestamp` pins both, along with the DECIMAL case that started this.
NUMERIC = re.compile(
    r"^(?:"
    r"U?(?:TINY|SMALL|BIG|HUGE)INT"  # TINYINT … HUGEINT, signed and unsigned
    r"|U?INT(?:EGER|\d+)?"  # INT, INT4, INT128, INTEGER, UINTEGER
    r"|DECIMAL|NUMERIC"  # fixed point, both spellings
    r"|DOUBLE|REAL|FLOAT\d*"  # floating point
    r"|SIGNED|SHORT|LONG"  # DuckDB's own aliases for the integer widths
    r")\b(?!\[)"
)

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


def extras() -> dict[tuple[str, str], str]:
    """`EXTRA_ADDITIVITY`, rekeyed to match `labelled()`."""
    return {
        (f"{schema}.{table}", column): label
        for (schema, table, column), label in EXTRA_ADDITIVITY.items()
    }


def everything() -> dict[tuple[str, str], str]:
    """Every label the release publishes — dbt's and the Polars layer's."""
    return labelled() | extras()


def numeric() -> dict[tuple[str, str], dict]:
    return {
        key: spec
        for key, spec in mart_columns().items()
        if NUMERIC.match((spec.get("data_type") or "").upper())
    }


def test_the_numeric_pattern_knows_a_measure_from_a_timestamp():
    """The pin for `NUMERIC`, which decides what the two coverage tests below
    even look at.

    It is the one thing here nothing else can check: a type absent from the
    contracts is invisible to every other assertion in this file, so the rule
    has to be exercised against types the warehouse does not hold *yet*. That is
    the whole failure the literal set had — no `DECIMAL`, no column to notice
    it, and a suite that went red on the correct label rather than the missing
    one.

    The near-misses are the point of the negative list: `INTERVAL` starts `INT`,
    `INTEGER[]` is a list of measures rather than a measure, and `STRUCT(…)`
    can contain one without being one.
    """
    measures = [
        "BIGINT",
        "INTEGER",
        "DOUBLE",
        "HUGEINT",
        "FLOAT",
        "REAL",
        "SMALLINT",
        "TINYINT",
        "UBIGINT",
        "UINTEGER",
        "UTINYINT",
        "INT",
        "INT4",
        "INT128",
        "DECIMAL(18,2)",
        "NUMERIC(10,4)",
        "FLOAT8",
    ]
    not_measures = [
        "VARCHAR",
        "BOOLEAN",
        "DATE",
        "TIMESTAMP",
        "TIMESTAMP WITH TIME ZONE",
        "INTERVAL",
        "BLOB",
        "UUID",
        "JSON",
        "INTEGER[]",
        "STRUCT(a INTEGER)",
    ]

    assert [t for t in measures if not NUMERIC.match(t)] == [], "measures the pattern missed"
    assert [t for t in not_measures if NUMERIC.match(t)] == [], "non-measures the pattern claimed"


def test_every_numeric_mart_column_carries_an_additivity_label():
    """The exhaustive half. A new measure with no label is the failure mode.

    This one is scoped to `marts`, which is what dbt can describe. The
    `analytics` tables are written by Polars and invisible to dbt, so they are
    declared in `EXTRA_ADDITIVITY` and held by the two tests below — the same
    split `EXTRA_CLASSIFICATIONS` makes for `pii`. `staging` is outside both on
    purpose: it is a cleaning copy of a source whose measures are declared one
    layer up.
    """
    missing = sorted(key for key in numeric() if key not in labelled())
    assert not missing, (
        f"{len(missing)} numeric mart columns carry no `meta: {{additivity: …}}`: {missing}"
    )


def test_the_label_vocabulary_is_closed():
    """Four labels, and a typo is a fifth. Same rule as the `pii` vocabulary:
    a label nobody chose deliberately is how a classification becomes
    decoration."""
    assert set(everything().values()) == LABELS


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
        for (relation, column), label in everything().items()
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
    assert flat == everything()


def test_a_copied_column_keeps_the_label_the_mart_gave_it():
    """`analytics.co2_intensity` is `select * from marts.fct_emissions_energy`
    plus two derived columns, so its labels are the mart's labels — and the
    reason they are copied into `EXTRA_ADDITIVITY` rather than inherited at
    runtime is that inheriting fails *open*.

    Rename or relabel a column in the mart and a derived map would follow it
    silently, publishing a `co2_intensity` that has quietly lost a label or
    gained a wrong one. Stated and asserted, the same rename fails here, naming
    both sides. Which is the general rule this repo already applies to every
    hand-maintained list: assert against the authority rather than derive from
    it, and take the restatement as the price.

    The set is checked as well as the values. A mart column added without a
    matching entry would otherwise leave one published column of a `select *`
    table unlabelled while every declared one still agreed.
    """
    mart = {
        column: label
        for (relation, column), label in labelled().items()
        if relation == "marts.fct_emissions_energy"
    }
    copy = {
        column: label
        for (relation, column), label in extras().items()
        if relation == "analytics.co2_intensity"
    }
    derived = {"co2_per_gdp_const_usd", "co2_intensity_rank"}

    assert set(copy) - derived == set(mart), (
        "`co2_intensity` is `select *` off the mart plus "
        f"{sorted(derived)}, so the two column sets must agree"
    )
    disagree = {c: (mart[c], copy[c]) for c in mart if mart[c] != copy[c]}
    assert not disagree, f"labelled differently in the mart and its copy: {disagree}"


def test_the_polars_output_is_labelled_where_dbt_cannot_see_it():
    """`analytics.retail_rfm` renames on the way in, which is what makes it the
    hard one: `frequency` is `dim_retail_customer.n_orders` and `monetary_gbp`
    is its `net_revenue_gbp`, so no rule that matches on column *name* can reach
    them from the mart — exactly the reason `EXTRA_CLASSIFICATIONS` names
    `monetary_gbp` by hand for `pii`.

    Coverage is checked against the frame the transform actually builds rather
    than against a list, so a column added to the RFM projection arrives here as
    a failure instead of as an unlabelled column in the release.
    """
    as_of = dt.date(2011, 12, 9)
    customers = pl.DataFrame(
        [
            {
                "customer_id": f"C{i}",
                "country": "United Kingdom",
                "country_iso3": "GBR",
                "cohort_month": "2010-01",
                "first_order_date": dt.date(2010, 1, 1),
                "last_order_date": dt.date(2011, 1, i + 1),
                "n_orders": i + 1,
                "net_revenue_gbp": 100.0 * (i + 1),
                "avg_order_value_gbp": 100.0,
                "n_distinct_products": 3 * (i + 1),
                "return_rate_pct": 0.0,
                "is_left_censored_cohort": False,
            }
            for i in range(10)
        ]
    )
    built = build_retail_rfm(customers, as_of)
    numeric_out = {column for column, dtype in built.schema.items() if dtype.is_numeric()}
    declared = {column for (relation, column) in extras() if relation == "analytics.retail_rfm"}
    assert numeric_out == declared, (
        "every numeric column the RFM transform emits must carry a label: "
        f"missing {sorted(numeric_out - declared)}, stale {sorted(declared - numeric_out)}"
    )
    assert extras()[("analytics.retail_rfm", "monetary_gbp")] == "additive", (
        "`monetary_gbp` is `dim_retail_customer.net_revenue_gbp` under another name"
    )
