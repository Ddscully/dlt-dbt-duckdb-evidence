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
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from ingest.pipeline import DUCKDB_PATH

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

Attribute the publishers, not this repository, when you use the numbers. Neither
the publishers nor this project warrant the data; the transformations are the
pipeline's and any error in them is ours.

The pipeline code is MIT licensed.
"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_sha() -> str | None:
    """The commit the export was built from, for reproducibility."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip() or None


def _repo_slug() -> str:
    """`owner/name`, for the download URLs in the release notes. Actions sets
    `GITHUB_REPOSITORY`; locally it comes off the origin remote so a fork's
    export links to the fork."""
    env = os.environ.get("GITHUB_REPOSITORY")
    if env:
        return env
    try:
        url = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "OWNER/REPO"
    match = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", url)
    return match.group(1) if match else "OWNER/REPO"


def default_tag(now: datetime | None = None) -> str:
    """Snapshot tags are dated, not semver: the schema is stable and it's the
    data that moves. Re-running on the same day overwrites the same release."""
    return f"data-{(now or datetime.now(UTC)):%Y-%m-%d}"


def published_tables(con: duckdb.DuckDBPyConnection) -> list[tuple[str, str]]:
    """Every table/view in the modelled schemas, dlt bookkeeping excluded."""
    rows = con.execute(
        """
        select table_schema, table_name
        from information_schema.tables
        where table_schema in (select unnest($schemas))
          and table_name not like '\\_%' escape '\\'
        order by table_schema, table_name
        """,
        {"schemas": list(PUBLISHED_SCHEMAS)},
    ).fetchall()
    return [(schema, table) for schema, table in rows]


def _loaded_at(con: duckdb.DuckDBPyConnection) -> str | None:
    """When the pipeline last landed data, which is not when this ran: an export
    of a stale warehouse should look stale."""
    try:
        row = con.execute("select max(inserted_at) from raw._dlt_loads").fetchone()
    except duckdb.Error:
        return None
    return row[0].astimezone(UTC).isoformat() if row and row[0] else None


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


def snapshot_warehouse(duckdb_path: Path, dest: Path) -> None:
    """Write a compacted, WAL-free copy of the warehouse to `dest`.

    `COPY FROM DATABASE` rather than a file copy so the result is consistent
    however the source was left (a crashed run can leave a `.wal` beside it), and
    so it's compacted rather than carrying the free-space of a `drop_resources`
    refresh. `dest.stem` has to stay `warehouse` — see the module docstring.
    """
    dest.unlink(missing_ok=True)
    con = duckdb.connect(str(dest))
    try:
        con.execute(f"attach '{duckdb_path}' as source_wh (read_only)")
        con.execute(f"copy from database source_wh to {dest.stem}")
        con.execute("detach source_wh")
    finally:
        con.close()


def _export_table(con: duckdb.DuckDBPyConnection, schema: str, table: str, out_dir: Path) -> dict:
    """Write one table as Parquet and describe it for the manifest."""
    qualified = f'"{schema}"."{table}"'
    path = out_dir / f"{schema}__{table}.parquet"
    con.execute(f"copy (select * from {qualified}) to '{path}' (format parquet, compression zstd)")

    columns = [c[0] for c in con.execute(f"describe {qualified}").fetchall()]
    (rows,) = con.execute(f"select count(*) from {qualified}").fetchone()
    entry = {
        "table": f"{schema}.{table}",
        "file": path.name,
        "rows": rows,
        "columns": len(columns),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }
    # Almost everything here is at (country_iso3, year) grain; the country
    # dimension isn't, so year coverage is reported only where it applies.
    if "year" in columns:
        bounds = con.execute(f"select min(year), max(year) from {qualified}").fetchone()
        entry["years"] = list(bounds) if bounds[0] is not None else None
    return entry


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

- **Grain:** one row per `(country_iso3, year)` in everything but
  `staging.stg_country`, which is the country dimension (region, income group).
- **Written by DuckDB {manifest["duckdb_version"]}.** Older clients may not read
  the storage format; the Parquet files have no such constraint.
- **Data last landed:** {manifest.get("data_loaded_at") or "unknown"}.
- **Revision history:** {history_note}. OWID restates published years;
  `history.snap_co2_estimates` (in the DuckDB file, not the Parquet) keeps every
  version it has served and `marts.fct_co2_estimate_versions` summarises them.
  Each release carries the previous one's history forward, so this accumulates.
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
    src = Path(duckdb_path)
    if not src.exists():
        raise FileNotFoundError(f"no warehouse at {src} — run `just run` first")

    tag = tag or default_tag()
    dest_dir = Path(out_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    # The copy keeps the source's file name (the catalog-name trap above), so an
    # `--out data` would delete the warehouse it was asked to package.
    warehouse_copy = dest_dir / src.name
    if warehouse_copy.resolve() == src.resolve():
        raise ValueError(f"--out {dest_dir} would overwrite the source warehouse {src}")
    snapshot_warehouse(src, warehouse_copy)

    # Read the tables out of the snapshot, not the original: it's the copy that
    # gets published, so the manifest should describe what shipped.
    con = duckdb.connect(str(warehouse_copy), read_only=True)
    try:
        manifest = {
            "tag": tag,
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "data_loaded_at": _loaded_at(con),
            "history": _history(con),
            "git_sha": _git_sha(),
            "duckdb_version": duckdb.__version__,
            "grain": "(country_iso3, year)",
            "warehouse": {
                "file": warehouse_copy.name,
                "bytes": warehouse_copy.stat().st_size,
                "sha256": _sha256(warehouse_copy),
            },
            "tables": [
                _export_table(con, schema, table, dest_dir)
                for schema, table in published_tables(con)
            ],
        }
    finally:
        con.close()

    (dest_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (dest_dir / "ATTRIBUTION.md").write_text(ATTRIBUTION)
    (dest_dir / "RELEASE_NOTES.md").write_text(release_notes(manifest, repo or _repo_slug(), tag))
    sums = [f"{manifest['warehouse']['sha256']}  {manifest['warehouse']['file']}"]
    sums += [f"{t['sha256']}  {t['file']}" for t in manifest["tables"]]
    (dest_dir / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    return manifest


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
