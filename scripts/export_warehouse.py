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

**The DuckDB copy must keep the file name `warehouse.duckdb`.** The `staging`
views were created by dbt against a catalog called `warehouse` (DuckDB names the
catalog after the file's stem) and their stored SQL says `warehouse.raw.owid_co2`.
Copy the database to `snapshot.duckdb` and every view raises
`Catalog "warehouse" does not exist`. Same trap for consumers, which is why the
release notes tell them to `ATTACH … AS warehouse`.

**Parquet ships the modelled layers only** (`staging`, `marts`, `analytics`).
`raw` is dlt's landing zone — snake_cased column names, load-id bookkeeping, the
WDI long format — and anyone who wants it can download the DuckDB file, which
carries everything.

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
    "MASKED_LABELS",
    "MAX_PUBLISHED_STORAGE_VERSION",
    "MIN_READER_VERSION",
    "PUBLISHED_SCHEMAS",
    "SALT_ENV",
    "default_tag",
    "main",
    "pseudonymise",
    "release_notes",
    "run",
]

EXPORT_DIR = "data/export"

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


def pseudonymise(
    con: duckdb.DuckDBPyConnection, manifest_path: str = MANIFEST_PATH, salt: str | None = None
) -> dict:
    """Rewrite every direct identifier in the published copy. Returns provenance.

    Runs against the copy, not the warehouse, and against *every* schema in it
    including `raw` — the published DuckDB file carries the landing tables, so a
    policy that stopped at the modelled layers would ship the original one schema
    away from the pseudonym.

    The columns are expanded by name before the rewrite, and the expansion is not
    a formality: 51 relations in this warehouse carry a `customer_id` where six
    are declared. Two kinds of thing sit in the difference, and neither would ever
    have been classified by hand:

    * **`raw_staging.retail_invoice_lines`** — dlt's merge scratch, a full copy of
      the landing table that no yml describes and nothing downstream reads. It
      shipped 1,067,371 rows, 824,364 of them with a clear id, inside every
      release made before this existed.
    * **44 `dbt_test__audit` tables.** `store_failures` is on project-wide, so
      every failing row of every retail test is written to a table that the
      published database then carries. They are empty today because the tests
      pass — which means the leak only opens on the day something goes wrong,
      and closes again before anyone looks.
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
| `{warehouse["file"]}` | the whole warehouse (`raw` + all of the below) | | | {size(warehouse["bytes"])} |
{chr(10).join(rows)}

`manifest.json` carries the row counts, year coverage and SHA-256 of every asset;
`SHA256SUMS` is `sha256sum -c`-compatible.

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
        prepare_copy=pseudonymise,
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
