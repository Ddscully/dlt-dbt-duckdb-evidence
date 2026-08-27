"""Package the built warehouse as a publishable artifact.

Produces `data/export/`:

  warehouse.duckdb                  a checkpointed copy of the whole warehouse
  <schema>__<table>.parquet         one file per modelled table (zstd)
  manifest.json                     row counts, year coverage, sha256, provenance
  SHA256SUMS                        `sha256sum -c`-compatible
  ATTRIBUTION.md                    who owns the data (it isn't us)
  RELEASE_NOTES.md                  the GitHub release body

Run:  uv run python -m scripts.export_warehouse            (or `just export`)

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
from modern_data_stack.export import default_tag, export
from modern_data_stack.paths import dbt_manifest_path, warehouse_path

DUCKDB_PATH = warehouse_path()
MANIFEST_PATH = dbt_manifest_path()

# `default_tag` is re-exported: `release-data.yml` and the tests name the tag
# through this module rather than reaching past it.
__all__ = [
    "ATTRIBUTION",
    "DUCKDB_PATH",
    "EXPORT_DIR",
    "EXTRA_CLASSIFICATIONS",
    "LAKEHOUSE_ASSET",
    "MASKED_LABELS",
    "MAX_PUBLISHED_STORAGE_VERSION",
    "MIN_READER_VERSION",
    "PUBLISHED_SCHEMAS",
    "SALT_ENV",
    "default_tag",
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
    ("analytics", "retail_rfm", "cohort_month"): "quasi_identifier",
    ("analytics", "retail_rfm", "first_order_date"): "quasi_identifier",
    ("analytics", "retail_rfm", "last_order_date"): "quasi_identifier",
    ("analytics", "retail_rfm", "monetary_gbp"): "quasi_identifier",
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


def publish_lakehouse(dest_dir: Path) -> dict:
    """Write the publishable landing tables to `<dest_dir>/lakehouse/`.

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

    # **Absent is recorded, never skipped silently.** A warehouse with no
    # lakehouse beside it is a legitimate thing to export — `tests/test_export.py`
    # builds one, and so would anyone packaging a database from elsewhere — but in
    # a *release* it means the landing zone did not get published, and the cost of
    # that lands a month later as a cold-started weather archive. So the manifest
    # carries `"rows": 0` and an empty table map rather than omitting the key,
    # which gives `release-data.yml` something to assert on. An absent key can
    # only be checked by code that remembers to look for it.
    if not lakehouse.is_catalog():
        return {"lakehouse": {"file": None, "tables": {}, "rows": 0}}

    with tempfile.TemporaryDirectory() as staging:
        built = Path(staging) / "lakehouse"
        copied = lakehouse.publish(built)
        # **A catalog holding none of the published tables is absent, not empty.**
        # dbt's `ATTACH IF NOT EXISTS` creates a real DuckLake — metadata table
        # and all — on any build that runs before the first ingest, so
        # `is_catalog()` is true and the copy is legitimately zero tables.
        # Shipping that would put a tarball of nothing in the release and let the
        # workflow's "was the landing zone published" assertion pass on it.
        if not copied:
            return {"lakehouse": {"file": None, "tables": {}, "rows": 0}}

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
            "bytes": archive.stat().st_size,
            "tables": copied,
            "rows": sum(copied.values()),
        }
    }


def prepare_published_copy(con: duckdb.DuckDBPyConnection) -> dict:
    """Everything the copy needs before it is read: stand alone, then anonymise.

    One hook because `export()` takes one, and the order is not interchangeable.
    `solidify_staging` writes eight new tables holding whatever their views
    selected — including, for the retail models, clear customer ids. Running the
    rewrite first would leave those eight untouched, and the copy would ship a
    `staging` layer that disagrees with the `marts` beside it about who a
    customer is, with matching row counts and no error anywhere.
    """
    return {**solidify_staging(con), **pseudonymise(con)}


def solidify_staging(con: duckdb.DuckDBPyConnection) -> dict:
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
    is a smaller promise: `_exposures.yml`'s release exposure names
    `stg_country`, and the release notes point a reader at it. Doing it *here*
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
        attach(
            con, catalog_path(LAKEHOUSE_DIR), data_path(LAKEHOUSE_DIR), ATTACH_ALIAS, read_only=True
        )
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
    builds (see `scripts/restore_history.py`), so this grows release over
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
  `staging.stg_country` is the country dimension (region, income group), the two
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
  row, because the source has no key linking the two.
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
- **Data last landed:** {manifest.get("data_loaded_at") or "unknown"}.
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
) -> dict:
    """Build `out_dir` from `duckdb_path`. Returns the manifest."""
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
        extra_manifest=lambda con: {"history": _history(con)},
        prepare_copy=prepare_published_copy,
        extra_artifacts=publish_lakehouse,
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
    assets = len(manifest["tables"]) + 1
    total = manifest["warehouse"]["bytes"] + sum(t["bytes"] for t in manifest["tables"])
    print(f"{manifest['tag']}: {assets} assets, {total / 1e6:.1f} MB in {args.out}")
    for table in manifest["tables"]:
        print(f"  {table['file']:44} {table['rows']:>8,} rows")


if __name__ == "__main__":
    main()
