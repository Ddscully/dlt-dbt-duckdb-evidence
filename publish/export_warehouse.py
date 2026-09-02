"""Package the built warehouse as a publishable artifact.

Produces `data/export/`:

  warehouse.duckdb                  a checkpointed copy of the whole warehouse
  <schema>__<table>.parquet         one file per modelled table (zstd)
  manifest.json                     row counts, year coverage, sha256, provenance
  SHA256SUMS                        `sha256sum -c`-compatible
  ATTRIBUTION.md                    who owns the data (it isn't us)
  RELEASE_NOTES.md                  the GitHub release body

Run:  uv run python -m publish.export_warehouse            (or `just export`)

`.github/workflows/release-data.yml` runs this after materializing the asset graph
against the live sources and uploads the directory as a dated GitHub release, so
the data is consumable without re-running the pipeline. Nothing here touches the
network: it reads a warehouse that already exists.

## Two things worth knowing

**The release ships what dbt builds, and nothing dlt landed.** `raw` lives in
the DuckLake catalog under `data/lakehouse/`, which is not published — so the
artifact is `staging`, `marts`, `analytics` and `history`, as Parquet for the
three modelled layers and as one DuckDB file for all of it. Two consequences:

* the file is materially smaller, and everything it lost is bookkeeping no
  consumer asked for — measured at **191 MB → 127 MB**, dropping 1.84M rows of
  landing tables and 1.30M of dlt's merge scratch;
* the largest privacy exposure this export was written to close is now
  impossible rather than handled. `raw_staging.retail_invoice_lines` carried
  824,364 clear customer ids into every release made before the policy existed;
  it cannot be in a file it is never written to.

**The `staging` views have to be materialised on the way out** — see
`solidify_staging`. dbt writes them against the attached catalog
(`lakehouse.raw.owid_co2`), so in a published file with no catalog they raise
`Catalog "lakehouse" does not exist!` while the marts beside them answer fine.

**The DuckDB copy must still keep the file name `warehouse.duckdb`.** DuckDB
names the catalog after the file's stem and the release notes tell consumers to
`ATTACH … AS warehouse`; the marts are self-contained, but a rename is exactly
the kind of half-broken artifact this module already guards against.

The packaging itself is `modern_data_stack.export`. What's here is the part that
is about *this* dataset: which schemas ship, who owns the data, the snapshot
summary in the manifest, and the release notes.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC
from pathlib import Path

import duckdb

from modern_data_stack import privacy
from modern_data_stack.ducklake import catalog_metadata
from modern_data_stack.export import default_tag, export, loaded_at
from modern_data_stack.paths import dbt_manifest_path, warehouse_path

DUCKDB_PATH = warehouse_path()
MANIFEST_PATH = dbt_manifest_path()

# `default_tag` is re-exported: `release-data.yml` and the tests name the tag
# through this module rather than reaching past it.
__all__ = [
    "ATTRIBUTION",
    "DUCKDB_PATH",
    "EXPORT_DIR",
    "EXTRA_ADDITIVITY",
    "EXTRA_CLASSIFICATIONS",
    "LAKEHOUSE_ASSET",
    "MASKED_LABELS",
    "MAX_PUBLISHED_LAKE_VERSION",
    "MAX_PUBLISHED_STORAGE_VERSION",
    "MIN_READER_VERSION",
    "PUBLISHED_SCHEMAS",
    "SALT_ENV",
    "additivity",
    "default_tag",
    "landed_at",
    "main",
    "prepare_published_copy",
    "pseudonymise",
    "publish_lakehouse",
    "release_notes",
    "run",
]

EXPORT_DIR = "data/export"

# The published landing zone, beside `warehouse.duckdb`. It exists so the *next*
# release can carry the capital-city weather archive forward instead of
# cold-starting it: `weather_watermark()` reads the destination, and a fresh
# runner's catalog is empty unless something puts rows in it. What may go in it
# is `lake.lakehouse.PUBLISHED_TABLES`, and that is an allowlist for a disclosure
# reason as well as a cost one — see there.
#
# **A tarball rather than a directory, because a GitHub release asset is a
# file.** A DuckLake is a catalog plus a tree of Parquet, and the alternatives
# are both worse: uploading the files individually needs a naming scheme and a
# reassembly step on the way back in, and the tree's shape is dlt's to choose,
# not ours. One asset also means one line in `SHA256SUMS`, so `sha256sum -c`
# still verifies the whole release.
LAKEHOUSE_ASSET = "lakehouse.tar.gz"

# The consumable layers. `raw` and dbt's `main` (the seed) are deliberately not
# here — see the module docstring.
PUBLISHED_SCHEMAS = ("staging", "marts", "analytics")

# The storage format the published `warehouse.duckdb` may not exceed, and the
# oldest client that can therefore open it. 64 is what every DuckDB from 0.10.0
# to 1.5.5 writes by default and the oldest format 1.5.5 still offers, so this
# ceiling costs nothing today — it is a tripwire, not a constraint.
#
# What it is a tripwire for: DuckDB 2.0 ships "a new default storage format",
# nothing in `pyproject.toml` caps `duckdb>=1.1`, and Dependabot's
# `versioning-strategy: lockfile-only` means the bump arrives as one line of a
# grouped monthly PR. Every test in this repo would pass it — they all write and
# read with the same binary — and the first symptom would be a consumer on 1.x
# unable to open next month's release. This is the repo's "green CI proves
# nothing about versions" one turn further round: the thing that moves is the
# *format of the artifact*, not the code.
#
# Raising it is a real decision with a date attached, not a lockfile edit: it
# strands every reader older than the new floor. The Parquet half of the release
# carries no such constraint, which is the argument for it having been there all
# along.
MAX_PUBLISHED_STORAGE_VERSION = 64
MIN_READER_VERSION = "0.10.0"

# The same promise for the *other* published artifact, and the reason it needs
# its own is that the two versions move for different reasons and are noticed at
# different moments.
#
# `warehouse.duckdb`'s format is decided by the `duckdb` in `uv.lock`, so its
# tripwire fires on a Dependabot PR — before the bump merges, with a person
# already reading the diff. The DuckLake spec is decided by a 36 MB binary from
# extensions.duckdb.org that no lockfile can name (`duckdb_extensions()` reports
# its version as a git hash), so **there is no PR to fail**: the extension can
# start writing a newer catalog schema with nothing in this repo changing at
# all. What notices is `just test` on the next CI run, which is why the
# toolchain half of this guard matters more here than it does for the file.
#
# 1.0 is what the installed extension writes today, so like the storage ceiling
# this costs nothing now and is a tripwire rather than a constraint. dlt's own
# `automatic_migration` defaults to False, so a catalog *we* write is safe by
# refusal; this covers the half dlt cannot see — a consumer meeting a tarball
# written against a spec their ducklake does not know.
MAX_PUBLISHED_LAKE_VERSION = "1.0"

ATTRIBUTION = """\
# Data attribution

This artifact is a *derived* dataset: the pipeline that produced it fetches
public data at run time, cleans it, and joins it. The underlying data belongs to
its publishers and is redistributed here under their licences.

| Source | Publisher | Licence |
|--------|-----------|---------|
| CO₂ and greenhouse-gas emissions | [Our World in Data](https://github.com/owid/co2-data) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| Energy production and consumption | [Our World in Data](https://github.com/owid/energy-data) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| World Development Indicators, country dimension | [World Bank](https://data.worldbank.org/) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| Household electricity prices | [Eurostat](https://ec.europa.eu/eurostat/databrowser/view/nrg_pc_204/) | [Eurostat reuse policy](https://ec.europa.eu/eurostat/help/copyright-notice) |
| CBAM default values (Annex I) | [Implementing Regulation (EU) 2025/2621](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=OJ%3AL_202502621), as corrected by [(EU) 2026/1740](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32026R1740) | [Commission reuse decision 2011/833/EU](https://eur-lex.europa.eu/eli/dec/2011/833/oj) |
| Euro foreign-exchange reference rates | [European Central Bank](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html), via [Frankfurter](https://frankfurter.dev) | [ECB reuse policy](https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html) |
| Online Retail II transactions | [UCI Machine Learning Repository](https://archive.ics.uci.edu/dataset/502/online+retail+ii) (Chen, D., 2019) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| Daily capital-city weather (ERA5 reanalysis) | [Open-Meteo](https://open-meteo.com/), generated using Copernicus Climate Change Service information (ECMWF ERA5) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |

Annexes II and III of that regulation — the country electricity emission factors
— are **not** in this artifact. They are IEA data under CC BY-NC-SA 4.0, and
carrying them would put a non-commercial and share-alike restriction on
everything here. `marts.dim_grid_emission_factors` is the OWID-derived analogue
and is not the same measurement; see `reports/pages/cbam.md`.

Attribute the publishers, not this repository, when you use the numbers. Neither
the publishers nor this project warrant the data; the transformations are the
pipeline's and any error in them is ours.

The pipeline code is MIT licensed.
"""


# Which classification gets rewritten on the way out. `quasi_identifier` columns
# are deliberately *not* in here: a country, a first-order date and a revenue
# figure identify a customer between them, but generalising any of them would
# destroy the analysis they exist for, and the honest answer is to publish them
# knowing that and to say so. `docs/DATA_PROTECTION.md` measures exactly how much
# they give away.
MASKED_LABELS = ("direct_identifier",)

# The salt is required, never defaulted, and this is the whole of the protection.
# `customer_id` is five digits: an unsalted digest of it is reversed by hashing
# the ten thousand possibilities, which is a few milliseconds. A missing salt
# therefore has to be an error rather than a fallback — a plausible-looking hex
# column that anyone can invert is worse than no column at all, because it
# reads as though something was done.
SALT_ENV = "PII_SALT"

# The classifications dbt cannot hold. `analytics` is written by Polars,
# downstream of dbt and invisible to it — the same boundary `tests/test_exposures.py`
# already proves for lineage, arriving here for the same reason. Named rather
# than inferred; the name-based sweep below would catch `customer_id` anyway, but
# a column renamed on the way into Polars would slip straight past it.
EXTRA_CLASSIFICATIONS: dict[tuple[str, str, str], str] = {
    ("analytics", "retail_rfm", "customer_id"): "direct_identifier",
    ("analytics", "retail_rfm", "country"): "quasi_identifier",
    ("analytics", "retail_rfm", "country_iso3"): "quasi_identifier",
    ("analytics", "retail_rfm", "cohort_month"): "quasi_identifier",
    ("analytics", "retail_rfm", "first_order_date"): "quasi_identifier",
    ("analytics", "retail_rfm", "last_order_date"): "quasi_identifier",
    ("analytics", "retail_rfm", "monetary_gbp"): "quasi_identifier",
}


# The additivity labels dbt cannot see, in exactly the shape and for exactly the
# reason `EXTRA_CLASSIFICATIONS` above has: the `analytics` tables are written by
# Polars, downstream of dbt and invisible to it, and they ship in the release
# beside the marts. Without these the map would stop at the layer boundary and a
# consumer would find five published tables with no labels at all.
#
# **Copied, not inherited at runtime, and that is the point.** Deriving
# `co2_intensity`'s 37 shared labels from `marts.fct_emissions_energy` would be
# less typing and would fail *open*: rename a column in the mart and the copy
# quietly loses its label with nothing to say so. Stated here, the same rename
# fails `test_a_copied_column_keeps_the_label_the_mart_gave_it`. Asserting rather
# than deriving is what every other hand-maintained list here does.
EXTRA_ADDITIVITY: dict[tuple[str, str, str], str] = {
    # `co2_intensity` is `select * from marts.fct_emissions_energy` plus two
    # derived columns, so its labels are the mart's plus two.
    ("analytics", "co2_intensity", "year"): "not_a_measure",
    ("analytics", "co2_intensity", "co2_mt"): "additive",
    ("analytics", "co2_intensity", "co2_per_capita"): "non_additive",
    ("analytics", "co2_intensity", "co2_kg_per_gdp_ppp_2011"): "non_additive",
    ("analytics", "co2_intensity", "share_global_co2"): "non_additive",
    ("analytics", "co2_intensity", "coal_co2"): "additive",
    ("analytics", "co2_intensity", "oil_co2"): "additive",
    ("analytics", "co2_intensity", "gas_co2"): "additive",
    ("analytics", "co2_intensity", "consumption_co2"): "additive",
    ("analytics", "co2_intensity", "consumption_co2_per_capita"): "non_additive",
    ("analytics", "co2_intensity", "trade_co2"): "additive",
    ("analytics", "co2_intensity", "trade_co2_share"): "non_additive",
    ("analytics", "co2_intensity", "cumulative_co2"): "semi_additive",
    ("analytics", "co2_intensity", "share_global_cumulative_co2"): "non_additive",
    ("analytics", "co2_intensity", "primary_energy_twh"): "additive",
    ("analytics", "co2_intensity", "renewables_share_pct"): "non_additive",
    ("analytics", "co2_intensity", "fossil_share_pct"): "non_additive",
    ("analytics", "co2_intensity", "electricity_generation_twh"): "additive",
    ("analytics", "co2_intensity", "carbon_intensity_elec_g_kwh"): "non_additive",
    ("analytics", "co2_intensity", "low_carbon_share_elec_pct"): "non_additive",
    ("analytics", "co2_intensity", "solar_share_elec_pct"): "non_additive",
    ("analytics", "co2_intensity", "wind_share_elec_pct"): "non_additive",
    ("analytics", "co2_intensity", "nuclear_share_elec_pct"): "non_additive",
    ("analytics", "co2_intensity", "coal_share_elec_pct"): "non_additive",
    ("analytics", "co2_intensity", "gas_share_elec_pct"): "non_additive",
    ("analytics", "co2_intensity", "gdp_per_capita_usd"): "non_additive",
    ("analytics", "co2_intensity", "gdp_usd"): "semi_additive",
    ("analytics", "co2_intensity", "gdp_constant_usd"): "additive",
    ("analytics", "co2_intensity", "life_expectancy"): "non_additive",
    ("analytics", "co2_intensity", "population"): "semi_additive",
    ("analytics", "co2_intensity", "poverty_rate"): "non_additive",
    ("analytics", "co2_intensity", "internet_users_pct"): "non_additive",
    ("analytics", "co2_intensity", "urban_pop_pct"): "non_additive",
    ("analytics", "co2_intensity", "forest_area_pct"): "non_additive",
    ("analytics", "co2_intensity", "renew_elec_pct"): "non_additive",
    ("analytics", "co2_intensity", "energy_imports_pct"): "non_additive",
    ("analytics", "co2_intensity", "electricity_price_eur_kwh"): "non_additive",
    # The two the Polars step adds. A dense rank is ordinal — summing ranks is
    # the classic way to turn an ordering into a number that means nothing.
    ("analytics", "co2_intensity", "co2_per_gdp_const_usd"): "non_additive",
    ("analytics", "co2_intensity", "co2_intensity_rank"): "non_additive",
    # `retail_rfm` renames on the way in, which is why these are stated by hand
    # rather than found by name: `frequency` is `dim_retail_customer.n_orders`
    # and `monetary_gbp` is its `net_revenue_gbp` — both additive, and neither
    # reachable from the mart by name. The three scores are quintiles: ordinal,
    # so `rfm_total` is a sum of ordinals and is still ordinal.
    ("analytics", "retail_rfm", "frequency"): "additive",
    ("analytics", "retail_rfm", "monetary_gbp"): "additive",
    ("analytics", "retail_rfm", "recency_days"): "non_additive",
    ("analytics", "retail_rfm", "recency_score"): "non_additive",
    ("analytics", "retail_rfm", "frequency_score"): "non_additive",
    ("analytics", "retail_rfm", "monetary_score"): "non_additive",
    ("analytics", "retail_rfm", "rfm_total"): "non_additive",
    ("analytics", "retail_rfm", "avg_order_value_gbp"): "non_additive",
    ("analytics", "retail_rfm", "n_distinct_products"): "non_additive",
    ("analytics", "retail_rfm", "return_rate_pct"): "non_additive",
    # The observability tables. `year_min`/`year_max` are calendar bounds and not
    # measures, the same answer `year` gets everywhere else.
    ("analytics", "pipeline_sources", "rows"): "additive",
    ("analytics", "pipeline_sources", "year_min"): "not_a_measure",
    ("analytics", "pipeline_sources", "year_max"): "not_a_measure",
    ("analytics", "pipeline_tables", "rows"): "additive",
    ("analytics", "pipeline_tables", "year_min"): "not_a_measure",
    ("analytics", "pipeline_tables", "year_max"): "not_a_measure",
    ("analytics", "pipeline_tests", "failing_rows"): "additive",
}


def classifications(manifest_path: str = MANIFEST_PATH) -> dict[tuple[str, str, str], str]:
    """Every classified column in the warehouse: dbt's, plus `analytics`.

    **Degrades when the manifest is absent rather than raising**, the same way
    `observability.manifest_tests` does and for the same reason: `dbt/target/` is
    gitignored, so a fresh clone has no manifest until something runs `dbt parse`
    — and `just test` runs before that step in CI.

    Degrading is only safe because of what the caller does next. `pseudonymise`
    expands whatever it gets *by column name* across every relation in the
    database, and `EXTRA_CLASSIFICATIONS` names `customer_id`, so every copy of
    the identifier is still found and still rewritten. What is lost is a *future*
    identifier that only dbt knows about and that shares no name with these —
    which is why the fallback is recorded in the published manifest rather than
    being silent.
    """
    path = Path(manifest_path)
    if not path.exists():
        return dict(EXTRA_CLASSIFICATIONS)
    return privacy.classifications(json.loads(path.read_text())) | EXTRA_CLASSIFICATIONS


def additivity(manifest_path: str = MANIFEST_PATH) -> dict[str, dict[str, str]] | None:
    """Which published columns may be summed, keyed by published relation.

    A consumer of a Parquet file has the column names and the types and nothing
    that says `renewables_share_pct` must not be summed while `co2_mt` may be —
    and 117 of the 226 numeric mart columns are non-additive. The labels
    are declared once, as `meta: {additivity: …}` on the column in the same ymls
    that carry the contract, and this is what carries them out of the repo.

    Keyed by `schema.alias`, so it names the relations the release actually
    ships: the versioned model appears as both `marts.fct_emissions_energy` and
    `marts.fct_emissions_energy_v1`, which is what the Parquet files are called.

    **Degrades to `None` when the manifest is absent** — `dbt/target/` is
    gitignored, so a fresh clone has none. `None` and `{}` are different answers
    here: the first says nobody asked dbt, the second would say dbt was asked and
    knows of no labelled column. Only the first can be true by accident.

    It drops `EXTRA_ADDITIVITY` on that path rather than publishing it alone,
    which is where this parts company with `classifications` — that one degrades
    to the extras *because* a partial answer still masks every identifier it
    names. Here a partial map is the more dangerous artifact: a consumer seeing
    `analytics` labelled and `marts` missing has no way to read the gap as
    "nobody asked" rather than as "nothing to say", and the default reading of a
    missing label is the wrong one.
    """
    path = Path(manifest_path)
    if not path.exists():
        return None
    nodes = json.loads(path.read_text()).get("nodes", {}).values()
    out: dict[str, dict[str, str]] = {}
    for node in nodes:
        if node.get("resource_type") != "model":
            continue
        labels: dict[str, str] = {}
        for column, spec in (node.get("columns") or {}).items():
            label = (spec.get("meta") or {}).get("additivity")
            if label:
                labels[column] = label
        if labels:
            relation = f"{node['schema']}.{node.get('alias') or node['name']}"
            out[relation] = labels
    for (schema, table, column), label in EXTRA_ADDITIVITY.items():
        out.setdefault(f"{schema}.{table}", {})[column] = label
    return {relation: dict(sorted(cols.items())) for relation, cols in sorted(out.items())}


def publish_lakehouse(dest_dir: Path, lakehouse_dir: str | Path | None = None) -> dict:
    """Write the publishable landing tables to `<dest_dir>/lakehouse/`.

    `lakehouse_dir` is a parameter rather than the module constant because
    reading the ambient one made this function's *output shape* depend on the
    developer's machine, and the test that covered it passed for that reason.
    See `tests/test_export.py`.

    A second release asset rather than a schema inside the first, because they
    are different kinds of thing: `warehouse.duckdb` is what dbt built and is
    reproducible from the sources, while this is the part that is not — the
    weather archive costs more than a day of Open-Meteo's allowance to refetch.
    Shipping it separately is also what lets it stay small: 44,936 rows in one
    Parquet file, against a database of marts.

    It is relocatable — relative `data_path`, so a consumer opens it with a bare
    `ATTACH` from wherever they unpacked it. `lake.lakehouse.restore` puts the
    absolute form back on the way in.
    """
    import tarfile
    import tempfile

    from lake import lakehouse

    lake_dir = lakehouse.LAKEHOUSE_DIR if lakehouse_dir is None else Path(lakehouse_dir)

    # **Absent is recorded, never skipped silently.** A warehouse with no
    # lakehouse beside it is a legitimate thing to export — `tests/test_export.py`
    # builds one, and so would anyone packaging a database from elsewhere — but in
    # a *release* it means the landing zone did not get published, and the cost of
    # that lands a month later as a cold-started weather archive. So the manifest
    # carries `"rows": 0` and an empty table map rather than omitting the key,
    # which gives `release-data.yml` something to assert on. An absent key can
    # only be checked by code that remembers to look for it.
    if not lakehouse.is_catalog(lake_dir):
        return {
            "lakehouse": {
                "file": None,
                "spec_version": None,
                "created_by": None,
                "tables": {},
                "rows": 0,
            }
        }

    with tempfile.TemporaryDirectory() as staging:
        built = Path(staging) / "lakehouse"
        copied = lakehouse.publish(built, lake_dir, MAX_PUBLISHED_LAKE_VERSION)
        # **A catalog holding none of the published tables is absent, not empty.**
        # dbt's `ATTACH IF NOT EXISTS` creates a real DuckLake — metadata table
        # and all — on any build that runs before the first ingest, so
        # `is_catalog()` is true and the copy is legitimately zero tables.
        # Shipping that would put a tarball of nothing in the release and let the
        # workflow's "was the landing zone published" assertion pass on it.
        if not copied:
            return {
                "lakehouse": {
                    "file": None,
                    "spec_version": None,
                    "created_by": None,
                    "tables": {},
                    "rows": 0,
                }
            }

        # Off the built catalog, before it is tarred: the manifest should
        # describe the artifact that shipped, not the lakehouse it came from.
        # `created_by` is a DuckDB git hash and answers "who wrote this";
        # `spec_version` is the one a consumer is actually asking about — the
        # same split as `duckdb_version` against `storage_version` above.
        catalog = catalog_metadata(built / lakehouse.CATALOG_NAME)

        archive = dest_dir / LAKEHOUSE_ASSET
        with tarfile.open(archive, "w:gz") as tar:
            # `arcname=""` would put the members at the archive root; naming the
            # directory keeps the tarball self-describing when someone opens it
            # by hand, and the restore strips it.
            tar.add(built, arcname="lakehouse")

    return {
        "lakehouse": {
            "file": LAKEHOUSE_ASSET,
            "catalog": lakehouse.CATALOG_NAME,
            "spec_version": catalog["version"],
            "created_by": catalog["created_by"],
            "bytes": archive.stat().st_size,
            "tables": copied,
            "rows": sum(copied.values()),
        }
    }


def prepare_published_copy(
    con: duckdb.DuckDBPyConnection, lakehouse_dir: str | Path | None = None
) -> dict:
    """Everything the copy needs before it is read: stand alone, then anonymise.

    One hook because `export()` takes one, and the order is not interchangeable.
    `solidify_staging` writes eight new tables holding whatever their views
    selected — including, for the retail models, clear customer ids. Running the
    rewrite first would leave those eight untouched, and the copy would ship a
    `staging` layer that disagrees with the `marts` beside it about who a
    customer is, with matching row counts and no error anywhere.

    `lakehouse_dir` is threaded through for the reason `landed_at` already
    documents: the hook is bound in `run()`, where the caller's choice of landing
    zone is known, rather than defaulted three frames down where it is not.
    """
    return {**solidify_staging(con, lakehouse_dir), **pseudonymise(con)}


def solidify_staging(
    con: duckdb.DuckDBPyConnection, lakehouse_dir: str | Path | None = None
) -> dict:
    """Turn the `staging` views into tables so the published file stands alone.

    `raw` lives in the DuckLake catalog, not in the DuckDB file, so dbt writes
    the staging views with the catalog spelled out —
    `select * from lakehouse.raw.owid_co2`. That is fine locally and fatal in a
    release: a consumer who opens the published file alone gets

        Binder Error: Catalog "lakehouse" does not exist!

    on every staging view, while the marts beside them answer normally. Measured
    by building exactly that and opening it — the marts returned 41 rows and the
    view raised.

    Materialising is the fix rather than dropping them, because the alternative
    is a smaller promise: the release ships all eight staging views as Parquet
    and a half-broken database is worse than a bigger one. Doing it *here*
    rather than making staging tables in `dbt_project.yml` keeps the local build
    cheap — eight views that cost nothing to rebuild — and pays for the copy only
    when a copy is made.

    Runs before `pseudonymise`, and that ordering is load-bearing in the opposite
    direction to the one this project used to document. When staging shipped as
    views they *recomputed* from a rewritten `raw`, so masking them too would
    have double-hashed. Now they are tables written from a `raw` that is not in
    the file at all, so they hold the original ids and the rewrite has to reach
    them — which it does, because it expands by column name across every schema.
    """
    from lake.lakehouse import ATTACH_ALIAS, LAKEHOUSE_DIR, catalog_path, data_path
    from modern_data_stack.ducklake import attach

    # The caller's landing zone, not the module constant. Reading `LAKEHOUSE_DIR`
    # here made the catalog this attaches independent of the database being
    # packaged: `export(duckdb_path=other, lakehouse_dir=other_lake)` solidified
    # `other`'s staging views against whichever lakehouse happened to be at
    # `./data/lakehouse` — the wrong rows where one existed, and an `IOException`
    # naming a path the caller never mentioned where it did not. The same defect
    # `landed_at` was fixed for, in the one call site that was missed.
    lake_dir = LAKEHOUSE_DIR if lakehouse_dir is None else Path(lakehouse_dir)

    defined = con.execute(
        "select view_name, sql from duckdb_views() where schema_name = 'staging' order by 1"
    ).fetchall()
    views = [name for name, _ in defined]
    if not views:
        return {"staging_views_materialised": []}

    # **Attach only if a view actually names the catalog.** Whether the lakehouse
    # is needed is a property of the view bodies, not of this project's layout,
    # and reading it off the SQL is what keeps the export working on a database
    # built some other way — `tests/test_export.py` writes `staging` views over a
    # `raw` schema in the same file, which is a perfectly exportable warehouse
    # that has no catalog anywhere near it. Requiring one would have made the
    # export refuse it, and the failure is an `IOException` about a path the
    # caller never mentioned.
    needs_catalog = any(f"{ATTACH_ALIAS}." in (sql or "") for _, sql in defined)
    if needs_catalog:
        attach(con, catalog_path(lake_dir), data_path(lake_dir), ATTACH_ALIAS, read_only=True)
    try:
        for name in views:
            # Two statements: DuckDB will not `create or replace table` over a
            # view of the same name, and the temp relation keeps the rows alive
            # across the drop.
            con.execute(
                f'create or replace table staging."_solid_{name}" as select * from staging."{name}"'
            )
            con.execute(f'drop view staging."{name}"')
            con.execute(f'alter table staging."_solid_{name}" rename to "{name}"')
    finally:
        if needs_catalog:
            con.execute(f"detach {ATTACH_ALIAS}")
    return {"staging_views_materialised": views}


def landed_at(
    con: duckdb.DuckDBPyConnection, lakehouse_dir: str | Path | None = None
) -> str | None:
    """`data_loaded_at` for the manifest, read from wherever `raw` actually is.

    dlt lands in the DuckLake catalog, so `raw._dlt_loads` is not in the file
    being packaged and the unqualified read that used to answer this now cannot.
    It did not start failing, which is the whole reason this exists: it raised
    `Catalog Error`, `loaded_at`'s `except` returned `None`, and every release
    body said "Data last landed: unknown." A tree migrated in place did worse and
    said something believable — see `loaded_at`.

    Attached read-only and only when there is a catalog to attach, which mirrors
    `solidify_staging` above and for the same reason: whether a lakehouse is
    involved is a property of the artifact, not an assumption this exporter gets
    to make. A warehouse that carries its own `raw` — `tests/test_export.py`
    builds one, and it is a perfectly legitimate thing to publish — still reads
    the in-file table. Where both exist the catalog wins, because on a migrated
    tree the in-file copy is the stale one.
    """
    from lake.lakehouse import ATTACH_ALIAS, LAKEHOUSE_DIR, catalog_path, data_path, is_catalog
    from modern_data_stack.ducklake import attach

    lake_dir = LAKEHOUSE_DIR if lakehouse_dir is None else Path(lakehouse_dir)
    if not is_catalog(lake_dir):
        return loaded_at(con)

    attach(con, catalog_path(lake_dir), data_path(lake_dir), ATTACH_ALIAS, read_only=True)
    try:
        return loaded_at(con, raw_database=ATTACH_ALIAS)
    finally:
        con.execute(f"detach {ATTACH_ALIAS}")


def pseudonymise(
    con: duckdb.DuckDBPyConnection, manifest_path: str = MANIFEST_PATH, salt: str | None = None
) -> dict:
    """Rewrite every direct identifier in the published copy. Returns provenance.

    Runs against the copy, not the warehouse, and against every schema in it. It
    no longer has to reach `raw`, because `raw` is not in the file — the landing
    tables live in the DuckLake catalog and the release does not ship it. That
    removes the single largest exposure this policy was written for by
    construction rather than by rule: `raw_staging.retail_invoice_lines`, dlt's
    merge scratch, cannot be in a file it was never written to.

    **The expansion by column name stays, and the reason it stays is the half of
    the gap that did not move.** It was written for two kinds of undeclared
    relation and only one of them was in `raw`:

    * **`raw_staging.retail_invoice_lines`** — dlt's merge scratch, a full copy of
      the landing table that no yml describes and nothing downstream reads. It
      shipped 1,067,371 rows, 824,364 of them with a clear id, inside every
      release made before the policy existed. It is now out of reach entirely.
    * **44 `dbt_test__audit` tables.** `store_failures` is on project-wide, so
      every failing row of every retail test is written to a table that the
      published database then carries. They are empty today because the tests
      pass — which means the leak only opens on the day something goes wrong,
      and closes again before anyone looks. **These are still in the file**, so
      the expansion is doing the same work it always did, over a smaller set.

    The `staging` tables that `solidify_staging` just wrote are the newest member
    of that set: they carry whatever their views carried, they are not declared,
    and they exist only in the copy.
    """
    salt = salt if salt is not None else os.environ.get(SALT_ENV, "")
    if not salt:
        raise privacy.PolicyError(
            f"{SALT_ENV} is not set. The export rewrites classified identifiers and an "
            "unsalted digest of a five-digit id is reversed in milliseconds, so there is "
            "no safe default. Set a stable secret for a release "
            "(`export PII_SALT=\"$(uv run python -c 'import secrets;print(secrets.token_hex(32))')\"` "
            "for a throwaway one)."
        )

    known = classifications(manifest_path)
    declared = sorted(c for c, label in known.items() if label in MASKED_LABELS)
    if not declared:
        # The one path that would otherwise be fail-*open*. A declared set of
        # nothing is what a typo in a `meta: {pii: …}` key looks like, and what a
        # dbt version that stopped surfacing column meta would look like, and what
        # an emptied `EXTRA_CLASSIFICATIONS` would look like. Each publishes every
        # identifier in the clear while `manifest.json` records that a policy was
        # applied, which is worse than having no policy at all.
        raise privacy.PolicyError(
            "no columns are classified as "
            f"{'/'.join(MASKED_LABELS)} — refusing to publish. Either the classification "
            "is broken or the labels have been renamed; an export that masks nothing "
            "must not look like one that masked everything."
        )
    columns = privacy.expand_by_name(con, declared)
    touched = privacy.apply_pseudonymisation(con, columns, salt)
    privacy.verify(con, columns)
    return {
        "privacy": {
            "policy": "salted-sha256",
            "labels_rewritten": list(MASKED_LABELS),
            "pseudonym_length": privacy.PSEUDONYM_LENGTH,
            # False when the export ran without a dbt manifest, which narrows the
            # declared set to `EXTRA_CLASSIFICATIONS`. A consumer can tell the two
            # apart; a release built by `release-data.yml` always has one.
            "declared_from_dbt_manifest": Path(manifest_path).exists(),
            "columns": [f"{s}.{t}.{c}" for s, t, c in touched],
        }
    }


def _history(con: duckdb.DuckDBPyConnection) -> dict | None:
    """How much revision history the published snapshot carries.

    `release-data.yml` restores `history` from the previous release before it
    builds (see `publish/restore_history.py`), so this grows release over
    release. `None` when there is no snapshot to describe at all — an export of
    a warehouse whose mart wasn't built, rather than one that has simply never
    seen a revision, which reports zero.
    """
    try:
        row = con.execute(
            """
            select
                count(*) as country_years,
                count(*) filter (where is_revised) as revised,
                sum(version_count) as versions,
                min(first_loaded_at) as watching_since
            from marts.fct_co2_estimate_versions
            """
        ).fetchone()
    except duckdb.Error:
        return None
    if not row or not row[0]:
        return None
    country_years, revised, versions, since = row
    return {
        "country_years": country_years,
        "revised": revised,
        "versions": versions,
        "watching_since": since.astimezone(UTC).isoformat(timespec="seconds") if since else None,
    }


def release_notes(manifest: dict, repo: str, tag: str) -> str:
    """The GitHub release body: what it is, how to query it, what's inside."""
    base = f"https://github.com/{repo}/releases"
    latest = f"{base}/latest/download"
    warehouse = manifest["warehouse"]
    # The ATTACH alias isn't cosmetic: it has to match the catalog the views were
    # created against, which DuckDB took from the file stem. Normally `warehouse`,
    # but a WAREHOUSE_PATH run can name the file anything.
    alias = Path(warehouse["file"]).stem
    mart = next(
        (t for t in manifest["tables"] if t["table"] == "marts.fct_emissions_energy"),
        manifest["tables"][0],
    )

    def size(n: int) -> str:
        return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{n / 1e3:.0f} kB"

    # Conditional for `history_note`'s reason: a release without a landing zone
    # is legitimate (the exporter records `file: None` rather than omitting the
    # key), and a bullet reading "spec None" is worse than no bullet.
    lake = manifest["lakehouse"]
    lake_version_note = (
        f"- **The landing zone has its own version, and it is not that one.** "
        f"`{LAKEHOUSE_ASSET}` is a DuckLake catalog written against **spec "
        f"{lake['spec_version']}** by {lake['created_by']}; what has to be new enough to open "
        f"it is your `ducklake` extension, not your DuckDB. `INSTALL ducklake` fetches the "
        f"build matching whatever DuckDB you are running, so in practice this only bites a "
        f"client pinned to an older extension.\n"
        if lake["file"]
        else ""
    )

    # The snapshot is the one table a rebuild can't reproduce, so say plainly how
    # much of it there is — including when the answer is "none yet".
    history = manifest.get("history")
    if history and history["revised"]:
        revisions = history["versions"] - history["country_years"]
        history_note = (
            f"{revisions:,} restatement{'s' if revisions != 1 else ''} across "
            f"{history['revised']:,} of {history['country_years']:,} country-years, "
            f"first recorded {(history.get('watching_since') or '')[:10] or 'unknown'}"
        )
    elif history:
        history_note = (
            f"{history['country_years']:,} country-years, all on version 1 — nothing restated yet"
        )
    else:
        history_note = "none — this snapshot is empty"

    rows = [
        f"| `{t['file']}` | `{t['table']}` | {t['rows']:,} | "
        f"{'–'.join(str(y) for y in t['years']) if t.get('years') else '—'} | {size(t['bytes'])} |"
        for t in manifest["tables"]
    ]

    published_lake = manifest.get("lakehouse") or {}
    lakehouse_row = (
        f"| `{published_lake['file']}` | the landing zone dlt wrote (DuckLake) "
        f"| {published_lake['rows']:,} | | {size(published_lake['bytes'])} |"
        if published_lake.get("file")
        else ""
    )

    return f"""\
Emissions, energy and development for ~200 countries at `(country_iso3, year)`
grain — the output of this repo's pipeline, so you can use the data without
running dlt, dbt, Polars or DuckDB yourself.

Built from the live sources on {manifest["generated_at"][:10]}, from commit
{f"`{manifest['git_sha'][:7]}`" if manifest.get("git_sha") else "the tip of `main`"}.

## Query it without downloading it

```sql
INSTALL httpfs; LOAD httpfs;
-- Alias it `{alias}`, not something shorter: the `staging` views store
-- fully-qualified SQL and resolve against that catalog name. Tables (`marts`,
-- `analytics`) read fine under any alias; the views raise
-- `Catalog "{alias}" does not exist`.
ATTACH '{latest}/{warehouse["file"]}' AS {alias} (READ_ONLY);
SELECT * FROM {alias}.{mart["table"]} LIMIT 10;
```

Or a single table, no DuckDB file at all:

```sql
SELECT country_name, year, co2_mt, renewables_share_pct
FROM read_parquet('{latest}/{mart["file"]}')
WHERE year = {mart["years"][1] if mart.get("years") else 2024};
```

Or just grab it:

```bash
curl -LO {latest}/{warehouse["file"]}
duckdb {warehouse["file"]} -c "select * from {mart["table"]} limit 5"
```

`latest/download/…` always points at the newest snapshot. Pin a run by swapping
it for `{base}/download/{tag}/…`.

## What's in it

| Asset | Table | Rows | Years | Size |
|-------|-------|-----:|-------|-----:|
| `{warehouse["file"]}` | everything below, plus `history` | | | {size(warehouse["bytes"])} |
{lakehouse_row}
{chr(10).join(rows)}

`manifest.json` carries the row counts, year coverage and SHA-256 of every asset;
`SHA256SUMS` is `sha256sum -c`-compatible.

**`manifest.json` also says which columns may be summed.** Its `additivity` map
labels every numeric column of every `marts` and `analytics` table published
here — 280 of them: `additive` (sum it in any direction),
`semi_additive` (summable some ways and not others — the column's own
description says which), `non_additive` (a ratio, rate, price, average or
extremum: recompute it from its components rather than aggregating it) and
`not_a_measure` (a key, a calendar part, or a parameter carried on the row).
Roughly half the numeric columns here are non-additive, which a Parquet file has
no way of telling you: `sum(renewables_share_pct)` and `avg(co2_per_capita)`
across countries are both meaningless and both come back a number.

**`raw` is not in `{warehouse["file"]}` any more.** dlt lands the source tables
in a [DuckLake](https://ducklake.select) catalog rather than in the database, so
the database holds what dbt built — `staging`, `marts`, `analytics`, `history` —
and the landing zone ships separately, as the tarball above. That asset carries
only what a rebuild cannot fetch again: the capital-city weather archive, which
costs more than a day of Open-Meteo's free-tier allowance. Unpack it and open it
where it lands — the catalog records a *relative* data path, so no
`OVERRIDE_DATA_PATH` is needed:

```sql
-- tar xzf lakehouse.tar.gz && cd lakehouse
install ducklake; load ducklake;
attach 'ducklake:duckdb:catalog.duckdb' as lakehouse;
select count(*) from lakehouse.raw.om_weather_daily;
```

- **Grain:** one row per `(country_iso3, year)`, with these exceptions —
  `marts.dim_country` is one row per country (names, region, income group,
  capital coordinates) and is what everything else's `country_iso3` joins to, the
  two
  `*_semiannual` tables keep Eurostat's published `(country_iso3, year, half)`,
  `marts.fct_example_scope2_emissions` is one row per site,
  `marts.fct_cbam_exposure` is one row per (sourcing country, good) with no year
  at all — it is a regulatory schedule, not a time series — the five FX
  tables below have no country in them, and the retail tables are per invoice
  line, product, customer or cohort month.
- **The retail tables are the one sub-country grain here.** `marts` carries
  `fct_retail_order_line` (one row per invoice line, priced in GBP, EUR and USD
  at the transaction date's ECB fixing), `dim_retail_product`,
  `dim_retail_customer`, `fct_retail_returns` and `fct_retail_customer_cohorts`;
  `analytics.retail_rfm` scores each customer. Three things to read first: a
  negative quantity on a *sale* invoice is a stock write-off and not a return
  (`is_stock_write_off`), 22.8% of lines carry no customer id so every
  per-customer table covers a subset of the business, and returns are matched to
  the sale they reverse by inference — `match_status` says how confidently, per
  row, because the source has no key linking the two. All four fact and
  dimension tables carry `country_iso3` beside the source's own country label,
  so retail can be joined to the country tables above — the source spells nine
  of its 43 countries in its own way (`EIRE`, `RSA`, `USA`, ...) and joining
  those two halves on a country *name* silently drops them.
- **`customer_id` is pseudonymised in this release and is stable across
  releases.** It is a salted digest of the publisher's own id, applied to every
  copy of the column in the file (`raw`, `staging`, `marts`, `analytics`), so it
  joins the retail tables to each other and to the previous release, and it does
  not join them back to the source workbook. The columns *beside* it are not
  masked and are not anonymous: 98.6% of the 5,881 customers are unique on
  `(first_order_gbp, net_revenue_gbp, n_orders)` alone. Treat these tables as
  personal data, because that is what they are — the reasoning is in
  [`docs/DATA_PROTECTION.md`](https://github.com/{repo}/blob/main/docs/DATA_PROTECTION.md).
- **The FX tables are the one daily grain here.** `marts.dim_date` is a calendar,
  `marts.dim_currency` is the currency dimension, and
  `marts.fct_fx_rates_published` / `_daily` / `_periods` are the ECB's euro
  reference rates as published, gap-filled, and aggregated to month / quarter /
  half / year. Two things to read before using them: the *daily* table carries a
  rate forward across weekends and holidays and marks it (`is_carried_forward`,
  `rate_source_date`), and the *periods* table ships both a period average and a
  period-end rate because converting a flow and converting a balance are not the
  same operation. Rates are quoted per euro, as the ECB quotes them; the
  reciprocal ships beside every one of them.
- **`marts.fct_cbam_exposure` is a screening tool, not a filing.** It prices Annex
  I of Implementing Regulation (EU) 2025/2621 — the CBAM default values an
  importer uses when they have no verified supplier data — at a carbon price that
  is a *parameter*, not a market quote. The price this build used is stated on
  every row in `ets_price_eur_per_t`, and the tonnage columns ship beside the euro
  columns so you can re-price without rebuilding.
  The seeds it is built from (`cbam_default_values`, `cbam_goods`,
  `cbam_markup_schedule`) are in the DuckDB file's `main` schema, not the Parquet
  set.
- **The CBAM annex was corrected, and this table changed shape with it.**
  Implementing Regulation (EU) 2026/1740 replaced Annexes I and IV in full on
  3 August 2026, retroactive to 1 January. **Two columns are gone**:
  `markup_is_inferred` and `markup_schedule_is_irregular` both described
  artefacts of the marked-up columns the annex used to publish and no longer
  does, so neither has a subject any more. There is no compatibility view for
  them — they cannot be reconstructed from the corrected annex at any price.
  The mark-up itself is unchanged in law (10 / 20 / 30%, fertilisers a flat 1%)
  and is now applied from the `cbam_markup_schedule` seed rather than read off
  the annex; `markup_2026_pct` still states it per row. The values moved very
  little — 66 of 10,503 comparable rows — but the good and country lists both
  changed: 10-digit TARIC codes now separate goods that shared a CN code, ten
  countries were relabelled, and Liberia and New Caledonia are new.
- **One table in here is invented and says so:**
  `marts.fct_example_scope2_emissions` prices twelve *hypothetical* sites against
  real grid emission factors, as a worked example of
  `marts.dim_grid_emission_factors`. Nothing else in this artifact is fabricated.
- **Any DuckDB from {MIN_READER_VERSION} on can open this file.** DuckDB {manifest["duckdb_version"]} wrote it,
  but the writer's version is not what decides whether you can read it — the storage
  format is, and this file is format {manifest["storage_version"]}, the oldest DuckDB still writes. If that
  ever rises the minimum reader version rises with it and this line will say so. The
  Parquet files carry no such constraint either way.
{lake_version_note}- **Data last landed:** {manifest.get("data_loaded_at") or "unknown"}.
- **Revision history (CO₂ estimates):** {history_note}. OWID restates published
  years; `history.snap_co2_estimates` (in the DuckDB file, not the Parquet) keeps
  every version it has served and `marts.fct_co2_estimate_versions` summarises
  them. `history.snap_grid_emission_factors` does the same for the Scope 2
  emission factors from 2015 on, summarised into the `first_published_*` and
  `is_restated` columns of `marts.dim_grid_emission_factors`. Each release
  carries the previous one's history forward, so both accumulate.
- **`co2_kg_per_gdp_ppp_2011` vs. `co2_per_gdp_const_usd`** are different bases
  (OWID's 2011 international-$ PPP vs. constant 2015 US$ derived here) and their
  levels are not comparable. Divide by `gdp_constant_usd`, never `gdp_usd`, for
  anything measured over time.
- **One column was renamed, and the old shape still ships.**
  `marts.fct_emissions_energy.co2_per_gdp` is `co2_kg_per_gdp_ppp_2011` from this
  release on — same numbers, a name that states the unit and the basis, because
  the column beside it in `analytics.co2_intensity` is a *different* basis and the
  old name said neither. `marts.fct_emissions_energy_v1` ships alongside with the
  old column name, and is removed on **2026-11-01**. If you read the mart, move to
  the new name before then; if you read nothing else in this artifact, nothing
  else moved.

## Licence

{ATTRIBUTION.split("# Data attribution", 1)[1].strip()}
"""


def run(
    duckdb_path: str = DUCKDB_PATH,
    out_dir: str = EXPORT_DIR,
    tag: str | None = None,
    repo: str | None = None,
    lakehouse_dir: str | Path | None = None,
) -> dict:
    """Build `out_dir` from `duckdb_path`. Returns the manifest.

    `lakehouse_dir` defaults to the project's, and exists so a caller packaging a
    database from somewhere else — or a test — can say which landing zone goes
    with it instead of picking up whichever one happens to be on the machine. It
    now decides three things rather than one: which catalog ships as the second
    asset, which one `data_loaded_at` is read from, and which one the `staging`
    views are materialised against. All three used to reach for the module
    constant, and the first one's test passed for the wrong reason because of it
    (see the module docstring) — so each is bound *here*, where the caller's
    choice is known, rather than defaulted inside the function that uses it.
    `solidify_staging` was the last one still reading the constant, and it is the
    one that decides what the published `staging` layer actually contains.
    """
    if not Path(duckdb_path).exists():
        raise FileNotFoundError(f"no warehouse at {duckdb_path} — run `just run` first")
    return export(
        duckdb_path,
        out_dir,
        schemas=PUBLISHED_SCHEMAS,
        attribution=ATTRIBUTION,
        release_notes=release_notes,
        tag=tag,
        repo=repo,
        grain="(country_iso3, year)",
        extra_manifest=lambda con: {"history": _history(con), "additivity": additivity()},
        read_loaded_at=lambda con: landed_at(con, lakehouse_dir),
        prepare_copy=lambda con: prepare_published_copy(con, lakehouse_dir),
        extra_artifacts=lambda dest: publish_lakehouse(dest, lakehouse_dir),
        max_storage_version=MAX_PUBLISHED_STORAGE_VERSION,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--warehouse", default=DUCKDB_PATH, help="source DuckDB file")
    parser.add_argument("--out", default=EXPORT_DIR, help="output directory")
    parser.add_argument("--tag", default=None, help="release tag (default: data-YYYY-MM-DD)")
    parser.add_argument(
        "--repo",
        default=None,
        help="owner/name for the download URLs (default: $GITHUB_REPOSITORY or the origin remote)",
    )
    args = parser.parse_args()

    manifest = run(args.warehouse, args.out, args.tag, args.repo)
    # Parquet, plus the database, plus the landing zone when there is one — the
    # count is printed to a person deciding whether the release looks right, so
    # a hardcoded `+ 1` that silently ignored the second asset would understate
    # exactly the artifact most likely to be missing.
    assets = len(manifest["tables"]) + 1 + (1 if manifest["lakehouse"]["file"] else 0)
    total = manifest["warehouse"]["bytes"] + sum(t["bytes"] for t in manifest["tables"])
    print(f"{manifest['tag']}: {assets} assets, {total / 1e6:.1f} MB in {args.out}")
    for table in manifest["tables"]:
        print(f"  {table['file']:44} {table['rows']:>8,} rows")


if __name__ == "__main__":
    main()
