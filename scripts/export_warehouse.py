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
from datetime import UTC
from pathlib import Path

import duckdb

from modern_data_stack.export import default_tag, export
from modern_data_stack.paths import warehouse_path

DUCKDB_PATH = warehouse_path()

# `default_tag` is re-exported: `release-data.yml` and the tests name the tag
# through this module rather than reaching past it.
__all__ = [
    "ATTRIBUTION",
    "DUCKDB_PATH",
    "EXPORT_DIR",
    "PUBLISHED_SCHEMAS",
    "default_tag",
    "main",
    "release_notes",
    "run",
]

EXPORT_DIR = "data/export"

# The consumable layers. `raw` and dbt's `main` (the seed) are deliberately not
# here — see the module docstring.
PUBLISHED_SCHEMAS = ("staging", "marts", "analytics")

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
| Household electricity prices | [Eurostat](https://ec.europa.eu/eurostat/databrowser/view/nrg_pc_204/) | [Eurostat reuse policy](https://ec.europa.eu/eurostat/about-us/policies/copyright) |
| CBAM default values (Annex I) | [Implementing Regulation (EU) 2025/2621](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=OJ%3AL_202502621) | [Commission reuse decision 2011/833/EU](https://eur-lex.europa.eu/eli/dec/2011/833/oj) |
| Euro foreign-exchange reference rates | [European Central Bank](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html), via [Frankfurter](https://frankfurter.dev) | [ECB reuse policy](https://www.ecb.europa.eu/services/using-our-site/copyright/html/index.en.html) |
| Online Retail II transactions | [UCI Machine Learning Repository](https://archive.ics.uci.edu/dataset/502/online+retail+ii) (Chen, D., 2019) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |

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
  The two seeds it is built from (`cbam_default_values`, `cbam_goods`) are in the
  DuckDB file's `main` schema, not the Parquet set.
- **One table in here is invented and says so:**
  `marts.fct_example_scope2_emissions` prices twelve *hypothetical* sites against
  real grid emission factors, as a worked example of
  `marts.dim_grid_emission_factors`. Nothing else in this artifact is fabricated.
- **Written by DuckDB {manifest["duckdb_version"]}.** Older clients may not read
  the storage format; the Parquet files have no such constraint.
- **Data last landed:** {manifest.get("data_loaded_at") or "unknown"}.
- **Revision history (CO₂ estimates):** {history_note}. OWID restates published
  years; `history.snap_co2_estimates` (in the DuckDB file, not the Parquet) keeps
  every version it has served and `marts.fct_co2_estimate_versions` summarises
  them. `history.snap_grid_emission_factors` does the same for the Scope 2
  emission factors from 2015 on, summarised into the `first_published_*` and
  `is_restated` columns of `marts.dim_grid_emission_factors`. Each release
  carries the previous one's history forward, so both accumulate.
- **`co2_per_gdp` vs. `co2_per_gdp_const_usd`** are different bases (OWID's 2011
  international-$ PPP vs. constant 2015 US$ derived here) and their levels are
  not comparable. Divide by `gdp_constant_usd`, never `gdp_usd`, for anything
  measured over time.

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
