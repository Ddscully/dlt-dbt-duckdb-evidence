"""The classification layer and the pseudonymisation at the publication boundary.

Two halves, and they check different kinds of thing.

The **mechanism** half builds a miniature warehouse in a tmp dir — a landing
table, a view over it and a mart, which is the shape that makes the boundary
policy hard — and asserts what the rewrite does to it. No real warehouse, no
network.

The **coverage** half reads the ymls as text. `just test` has no dbt manifest
(it is gitignored and built later in CI), so the same constraint that shapes
`tests/test_exposures.py` applies here: the declarations are checked as
documents. What it enforces is not "every column is labelled" — that would be
paperwork — but that a column *sharing a name with a classified one* is never
left silently unlabelled, which is the way a classification actually rots.
"""

from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pytest
import yaml

from modern_data_stack import privacy
from modern_data_stack.paths import project_root
from publish.export_warehouse import EXTRA_CLASSIFICATIONS, MASKED_LABELS, SALT_ENV, pseudonymise

LABELS = {"direct_identifier", "quasi_identifier", "non_personal"}

MARTS_DIR = project_root() / "dbt" / "models" / "marts"
STAGING_YML = project_root() / "dbt" / "models" / "staging" / "_staging.yml"
SOURCES_YML = project_root() / "dbt" / "models" / "staging" / "_sources.yml"

# The shape the boundary has to survive: a landing table, dlt's undeclared copy
# of it, a *view* reading the landing table, and a mart materialised from the
# view. Mask the tables only and the view has to still agree with the mart.
SETUP = """
create schema raw;
create schema raw_staging;
create schema staging;
create schema marts;

create table raw.lines as select * from (values
    ('12345', 'GBP', 10.0),
    ('12345', 'GBP', 5.0),
    ('67890', 'GBP', 7.0),
    (NULL,    'GBP', 3.0)
) t(customer_id, currency, amount);

create table raw_staging.lines as select * from raw.lines;

create view staging.stg_lines as select * from raw.lines;

create table marts.dim_customer as
    select customer_id, sum(amount) as revenue
    from staging.stg_lines where customer_id is not null group by 1;
"""

DECLARED = [
    ("raw", "lines", "customer_id"),
    ("staging", "stg_lines", "customer_id"),
    ("marts", "dim_customer", "customer_id"),
]


@pytest.fixture
def warehouse(tmp_path):
    con = duckdb.connect(str(tmp_path / "w.duckdb"))
    con.execute(SETUP)
    yield con
    con.close()


# --------------------------------------------------------------------------
# The mechanism
# --------------------------------------------------------------------------


def test_a_null_identifier_stays_null_because_the_expression_uses_pipes(warehouse):
    """The `concat` trap, pinned rather than described.

    DuckDB's `concat` *ignores* NULL arguments, so `concat(customer_id, $salt)`
    on a row with no customer hashes the bare salt — and every anonymous row in
    the file lands on one identical pseudonym that is indistinguishable from a
    real customer. Here that would silently invent a shopper with 243,007 order
    lines. `||` propagates, which is the truth: no identifier, no pseudonym.
    """
    naive, correct = warehouse.execute(
        "select substr(sha256(concat(customer_id, $salt)), 1, 16), "
        f"{privacy.pseudonym_expr('customer_id')} "
        "from raw.lines where customer_id is null",
        {"salt": "s"},
    ).fetchone()
    assert naive is not None, "if this ever becomes None, the trap is gone and so is this test"
    assert correct is None


def test_a_missing_salt_is_an_error_and_never_a_default(warehouse):
    with pytest.raises(privacy.PolicyError, match="salt"):
        privacy.apply_pseudonymisation(warehouse, DECLARED, salt="")


def test_the_exporter_refuses_to_run_without_a_salt(warehouse, monkeypatch):
    """Fail closed at the boundary too, not only in the mechanism: an export that
    quietly published clear identifiers because an environment variable was unset
    is exactly the failure the policy exists to prevent."""
    monkeypatch.delenv(SALT_ENV, raising=False)
    with pytest.raises(privacy.PolicyError, match=SALT_ENV):
        pseudonymise(warehouse)


def test_the_same_identifier_gets_the_same_pseudonym_and_a_new_salt_changes_it(tmp_path):
    """Two *independent* copies, one salt each.

    Re-salting the same connection would hash the pseudonyms rather than the
    ids, and a double hash differs from a single one whatever the salt is — so
    the second assertion would hold even for an expression that dropped the salt
    entirely, which is precisely the bug it exists to catch.
    """

    def build(salt: str, run: int = 0) -> list[tuple]:
        con = duckdb.connect(str(tmp_path / f"{salt}-{run}.duckdb"))
        con.execute(SETUP)
        privacy.apply_pseudonymisation(con, DECLARED, salt=salt)
        rows = con.execute("select customer_id from marts.dim_customer order by 1").fetchall()
        distinct = con.execute("select count(distinct customer_id) from raw.lines").fetchone()
        con.close()
        assert distinct == (2,), "two customers in, two pseudonyms out"
        return rows

    assert build("one", 1) == build("one", 2), "the same salt has to be reproducible"
    assert build("one", 3) != build("two", 4)


def test_a_view_and_the_mart_below_it_still_agree_after_the_rewrite(warehouse):
    """The reason the policy is applied to the finished copy and not in a model.

    `staging.stg_lines` is a view: in the published database it recomputes from
    `raw.lines` whenever a consumer queries it. If the mask were applied in the
    model instead, the shipped view would hash a value the shipped mart had
    already hashed, and the two would disagree about who each customer is —
    with matching row counts and no error anywhere.
    """
    privacy.apply_pseudonymisation(warehouse, DECLARED, salt="s")
    (matched,) = warehouse.execute(
        "select count(*) from staging.stg_lines s join marts.dim_customer d using (customer_id)"
    ).fetchone()
    assert matched == 3, "every identified line still finds its customer"


def test_the_sweep_finds_the_copy_nobody_declared(warehouse):
    """`raw_staging` is dlt's merge scratch. No yml describes it, nothing reads
    it, and it holds a full copy of the landing table — including, before this,
    824,364 clear customer ids in every release."""
    found = privacy.expand_by_name(warehouse, DECLARED)
    assert ("raw_staging", "lines", "customer_id") in found

    privacy.apply_pseudonymisation(warehouse, found, salt="s")
    privacy.verify(warehouse, found)


def test_verify_catches_a_column_the_rewrite_missed(warehouse):
    privacy.apply_pseudonymisation(warehouse, DECLARED, salt="s")
    with pytest.raises(privacy.PolicyError, match=r"raw_staging\.lines\.customer_id"):
        privacy.verify(warehouse, privacy.expand_by_name(warehouse, DECLARED))


def test_a_view_is_left_to_recompute_rather_than_updated(warehouse):
    touched = privacy.apply_pseudonymisation(warehouse, DECLARED, salt="s")
    assert ("staging", "stg_lines", "customer_id") not in touched


def test_k_anonymity_counts_the_rows_that_are_alone(warehouse):
    measured = privacy.k_anonymity(warehouse, "raw.lines", ["customer_id"])
    # Two lines share 12345; 67890 and the anonymous row are each alone.
    assert measured == {"singletons": 2, "rows": 4, "largest_group": 2}


def test_classifications_reads_models_and_sources_and_resolves_the_relation():
    """The manifest, not the yml, because a project overriding
    `generate_schema_name` — as this one does — has ymls that never mention the
    schema a model lands in."""
    manifest = {
        "nodes": {
            "model.p.stg_lines": {
                "schema": "staging",
                "name": "stg_lines",
                "alias": "stg_lines",
                "columns": {"customer_id": {"meta": {"pii": "direct_identifier"}}},
            },
            "model.p.other": {"schema": "marts", "name": "other", "columns": {}},
        },
        "sources": {
            "source.p.raw.lines": {
                "schema": "raw",
                "identifier": "lines",
                "name": "lines",
                "columns": {"country": {"meta": {"pii": "quasi_identifier"}}},
            }
        },
    }
    assert privacy.classifications(manifest) == {
        ("staging", "stg_lines", "customer_id"): "direct_identifier",
        ("raw", "lines", "country"): "quasi_identifier",
    }
    assert privacy.classified_columns(manifest, ["direct_identifier"]) == [
        ("staging", "stg_lines", "customer_id")
    ]


# --------------------------------------------------------------------------
# The declarations
# --------------------------------------------------------------------------


def yml_classifications(path) -> dict[tuple[str, str], str]:
    """`{(model, column): label}` for one models-shaped yml."""
    parsed = yaml.safe_load(path.read_text())
    return {
        (model["name"], column["name"]): (column.get("meta") or {})["pii"]
        for model in parsed["models"]
        for column in model.get("columns") or []
        if (column.get("meta") or {}).get("pii")
    }


def yml_columns(path) -> dict[str, dict]:
    """`{model: {"group": …, "columns": [names]}}`."""
    parsed = yaml.safe_load(path.read_text())
    return {
        model["name"]: {
            "group": (model.get("config") or {}).get("group"),
            "columns": [c["name"] for c in model.get("columns") or []],
        }
        for model in parsed["models"]
    }


def marts_ymls() -> list[Path]:
    """Every models-shaped yml in the marts folder.

    A glob rather than a named file: this was one 2,183-line `_marts.yml` until
    it was split per dbt group, and the split is exactly the kind of change that
    leaves a coverage test reading three quarters of the tree while still
    passing. A fifth group file has to be seen without anyone remembering to add
    it here. `_unit_tests.yml` carries no `models:` key and drops out on its own.
    """
    return sorted(
        p for p in MARTS_DIR.glob("*.yml") if (yaml.safe_load(p.read_text()) or {}).get("models")
    )


def models_yml_paths() -> list[Path]:
    return [*marts_ymls(), STAGING_YML]


def merged(reader) -> dict:
    out: dict = {}
    for path in models_yml_paths():
        out |= reader(path)
    return out


def declared() -> dict[tuple[str, str], str]:
    return merged(yml_classifications)


def test_the_yml_scan_finds_every_mart_it_should():
    """The vacuity guard for the glob above.

    Two of the three coverage tests below assert an *absence* — no unlabelled
    name collision, no unclassified copy of the identifier — so both would pass
    on an empty scan, which is precisely what a mistyped suffix or a fifth file
    in a subfolder would produce. Deriving the expected set from the `.sql`
    files means a model whose yml block went missing in a move is a failure
    rather than a silence.

    `fct_emissions_energy` is the one collapse: `_v1.sql` and `_v2.sql` are two
    files under one `versions:` entry, so the suffix comes off before comparing.
    """
    declared_models = set(merged(yml_columns))
    on_disk = {re.sub(r"_v\d+$", "", p.stem) for p in MARTS_DIR.glob("*.sql")}

    assert len(marts_ymls()) >= 2, "the marts glob collapsed back to a single file"
    assert on_disk - declared_models == set(), (
        f"marts models with no yml block anywhere: {sorted(on_disk - declared_models)}"
    )


def test_the_label_vocabulary_is_closed():
    """Three labels, and a typo is a new one. `docs/DATA_PROTECTION.md` defines
    them; a fourth appearing here without a decision behind it is how a
    classification becomes decoration."""
    assert set(declared().values()) | set(EXTRA_CLASSIFICATIONS.values()) == LABELS


def test_a_column_named_like_a_classified_one_is_never_left_unlabelled():
    """The rule that gives the classification teeth.

    Labelling all 90-odd retail columns would be paperwork nobody reads. What
    actually rots is narrower and specific: `dim_retail_product.net_revenue_gbp`
    is revenue per *product* and carries no personal data, while
    `dim_retail_customer.net_revenue_gbp` singles out 97.4% of customers on its
    own. Same name, opposite answer — so the distinction has to be written down
    rather than inferred from one of them being blank.
    """
    labelled = declared()
    names = {column for _, column in labelled}
    models = merged(yml_columns)

    missing = {
        (model, column)
        for model, spec in models.items()
        if spec["group"] == "retail"
        for column in spec["columns"]
        if column in names and (model, column) not in labelled
    }
    assert not missing, f"share a name with a classified column but carry no label: {missing}"


def test_the_identifier_is_classified_everywhere_it_appears():
    """A direct identifier is the one label with a *consequence* — it is what the
    export rewrites — so an unclassified copy of it is not a documentation gap,
    it is a column that ships in the clear."""
    labelled = declared()
    identifiers = {column for (_, column), label in labelled.items() if label in MASKED_LABELS}
    models = merged(yml_columns)

    for model, spec in models.items():
        for column in spec["columns"]:
            if column in identifiers:
                assert labelled.get((model, column)) in MASKED_LABELS, (
                    f"{model}.{column} is a copy of a direct identifier and is not labelled one"
                )


def test_the_landing_table_declares_the_identifier():
    """Classification starts at the source, not at staging: `raw` ships inside
    the published DuckDB file, so a policy beginning one layer down has already
    let the clear value into the artifact."""
    parsed = yaml.safe_load(SOURCES_YML.read_text())
    tables = {table["name"]: table for source in parsed["sources"] for table in source["tables"]}
    columns = {c["name"]: c for c in tables["retail_invoice_lines"]["columns"]}
    assert columns["customer_id"]["meta"]["pii"] == "direct_identifier"


def test_the_polars_output_is_classified_where_dbt_cannot_see_it():
    """`analytics.retail_rfm` is written by Polars, downstream of dbt and
    invisible to it — the same boundary `tests/test_exposures.py` proves for
    lineage. The name-based sweep would catch `customer_id` at export time
    anyway; what it could not catch is a column renamed on the way into Polars,
    which is why `monetary_gbp` is named here explicitly."""
    rfm = {
        column: label
        for (_, table, column), label in EXTRA_CLASSIFICATIONS.items()
        if table == "retail_rfm"
    }
    assert rfm["customer_id"] == "direct_identifier"
    assert rfm["monetary_gbp"] == "quasi_identifier", (
        "`monetary_gbp` is `dim_retail_customer.net_revenue_gbp` under another name, "
        "and it identifies 97.4% of customers on its own"
    )


def test_a_second_application_is_refused_rather_than_double_hashing(warehouse):
    """Hashing a pseudonym gives another 16 hex characters, so `verify` cannot
    tell one application from two after the fact — which makes a re-run, or a run
    pointed at the real warehouse instead of a copy, silent and unrecoverable.
    The check has to happen before the write."""
    privacy.apply_pseudonymisation(warehouse, DECLARED, salt="s")
    with pytest.raises(privacy.PolicyError, match="already holds"):
        privacy.apply_pseudonymisation(warehouse, DECLARED, salt="s")


def test_an_empty_classified_set_is_refused_at_the_boundary(warehouse, monkeypatch):
    """The one path that would otherwise be fail-*open*: nothing classified reads
    exactly like nothing to classify, and publishes every identifier in the clear
    while the manifest records that a policy was applied."""
    monkeypatch.setattr("publish.export_warehouse.EXTRA_CLASSIFICATIONS", {})
    with pytest.raises(privacy.PolicyError, match="refusing to publish"):
        pseudonymise(warehouse, manifest_path="/nonexistent/manifest.json", salt="s")


def test_a_missing_manifest_degrades_instead_of_raising(warehouse, monkeypatch):
    """`dbt/target/` is gitignored and `just test` runs before `dbt parse`, so an
    exporter that required a manifest broke every test in `tests/test_export.py`
    on a fresh clone. Degrading is safe only because the sweep is by column name:
    `EXTRA_CLASSIFICATIONS` still names `customer_id`, so every copy of it is
    still found."""
    monkeypatch.setattr(
        "publish.export_warehouse.EXTRA_CLASSIFICATIONS",
        {("marts", "dim_customer", "customer_id"): "direct_identifier"},
    )
    provenance = pseudonymise(warehouse, manifest_path="/nonexistent/manifest.json", salt="s")
    assert provenance["privacy"]["declared_from_dbt_manifest"] is False
    assert ("raw", "lines", "customer_id") in [
        tuple(c.split(".")) for c in provenance["privacy"]["columns"]
    ], "the sweep still reaches the landing table nobody named"
