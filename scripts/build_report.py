"""Build the Evidence site — the one layer that was still built by hand.

Wraps the two npm commands the dashboard needs, in the order it needs them:

    npm ci / npm install          Evidence + its DuckDB adapter
    npm run sources[:strict]      warehouse tables -> reports/.evidence/ parquet
    npm run build                 parquet + markdown -> reports/build/ (static)

Run:  uv run python -m scripts.build_report            (or `just report`)
      uv run python -m scripts.build_report --clean    (or `just report-clean`)

This exists so the site is reachable from the asset graph
(`reports/evidence_site` in `orchestration/assets.py`) rather than only from a
shell recipe, and so both callers run *the same* three commands. Everything below
is knowledge that was previously spread across the justfile, the Pages workflow
and `reports/README.md`.

## Why `sources` is a separate step from `build`

`evidence build` does not run the sources. It renders against whatever parquet
`reports/.evidence/` already holds, and that directory is gitignored — so a build
from a cold clone *succeeds* and produces a site where every chart reads "Table
with name emissions_energy does not exist". `--strict` (the default here) turns an
empty or missing warehouse into a non-zero exit instead of a green build of a
blank dashboard.

## Why `--clean` exists, and what happens without it

Evidence caches each source's schema keyed on the source SQL. A `select *` that
gains a column is unchanged as a string, so the cache keeps the old schema and
validation fails against it. Deleting `.evidence/` is the fix; it is only ever
needed locally, since CI always starts cold.

`build/` is a different matter and is cleared on *every* run, `--clean` or not:
`evidence build` writes into the directory without emptying it first, so repeated
builds pile up orphaned `_app/immutable/` chunks (139 of them after three runs
here) and a renamed page goes on serving from its old route.

Clearing it makes the reported size real, not the output reproducible: the file
count still drifts by one or two between local runs, because Evidence emits an
`api/` route per query hash and the `pipeline_*` queries carry load timestamps
that change. Nothing stale survives in `build/` — every file in it is from the
run that just finished — so this is cosmetic, and a cold CI checkout doesn't see
it at all.

## What it does not do

It does not touch `evidence.config.yaml`. GitHub Pages serves a project site from
a subpath and Evidence reads that from `deployment.basePath`, which has no
env-var equivalent — so `pages.yml` appends it before calling this, and a
committed value would break `npm run dev` on localhost.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path

from modern_data_stack.paths import project_root

REPORTS_DIR = project_root() / "reports"

# Evidence's own layout, not ours: `pages/x.md` renders to `build/x/index.html`
# (and `pages/index.md` to `build/index.html`), sources live in `sources/<source>/`,
# and the extracted parquet lands in `.evidence/`. These are the defaults; `run()`
# takes the reports directory, so the tests can point the parsers at a temp tree.
PAGES_DIR = REPORTS_DIR / "pages"
SOURCES_DIR = REPORTS_DIR / "sources"
BUILD_DIR = REPORTS_DIR / "build"

# Schemas a source query can legitimately read. Used to pull the warehouse
# dependencies out of the SQL — see `source_tables`.
WAREHOUSE_SCHEMAS = ("raw", "staging", "marts", "analytics", "history")

# What writes each table the source queries read. `orchestration/assets.py` turns
# these two into the Evidence asset's deps — dbt models by model name (their
# Dagster keys come out of the manifest, so they can't be spelled here), the
# Polars outputs by asset key. Three tables share one key: `pipeline_status`
# writes all three in a single op.
#
# `history.snap_*` is a legal schema for a source query to read but no page does:
# the snapshots reach the site through the marts that summarise them
# (`fct_co2_estimate_versions`, `dim_grid_emission_factors`), which is also the
# only shape `get_asset_key_for_model` can resolve.
#
# They live here rather than beside the asset so `tests/test_report.py` can check
# them against the SQL without importing Dagster — which `just test` cannot do,
# because the dbt manifest it needs is gitignored and built later in CI.
TABLE_TO_DBT_MODEL = {
    "marts.dim_country_year": "dim_country_year",
    "marts.dim_currency": "dim_currency",
    "marts.dim_date": "dim_date",
    "marts.dim_grid_emission_factors": "dim_grid_emission_factors",
    "marts.dim_retail_customer": "dim_retail_customer",
    "marts.fct_cbam_exposure": "fct_cbam_exposure",
    "marts.fct_co2_estimate_versions": "fct_co2_estimate_versions",
    "marts.fct_emissions_energy": "fct_emissions_energy",
    "marts.fct_eu_electricity_prices_semiannual": "fct_eu_electricity_prices_semiannual",
    "marts.fct_example_scope2_emissions": "fct_example_scope2_emissions",
    "marts.fct_fx_rates_daily": "fct_fx_rates_daily",
    "marts.fct_fx_rates_periods": "fct_fx_rates_periods",
    "marts.fct_fx_rates_published": "fct_fx_rates_published",
    "marts.fct_retail_customer_cohorts": "fct_retail_customer_cohorts",
    "marts.fct_retail_order_line": "fct_retail_order_line",
    "marts.fct_retail_returns": "fct_retail_returns",
}

TABLE_TO_ASSET_KEY = {
    "analytics.co2_intensity": ("analytics", "co2_intensity"),
    "analytics.pipeline_sources": ("analytics", "pipeline_status"),
    "analytics.pipeline_tables": ("analytics", "pipeline_status"),
    "analytics.pipeline_tests": ("analytics", "pipeline_status"),
    "analytics.retail_rfm": ("analytics", "retail_rfm"),
}

_TABLE_REF = re.compile(
    rf"\b(?:from|join)\s+(({'|'.join(WAREHOUSE_SCHEMAS)})\.[a-z_][a-z_0-9]*)",
    re.IGNORECASE,
)
_SQL_COMMENT = re.compile(r"--[^\n]*")
# A page reads `from warehouse.<query>` — Evidence's own spelling, where the
# prefix is the directory under `sources/`. Matched loosely and filtered against
# the source names that exist, so `from warehouse.typo` is an error rather than
# something quietly skipped.
_QUERY_REF = re.compile(r"\b(?:from|join)\s+([a-z_][a-z_0-9]*\.[a-z_][a-z_0-9]*)", re.IGNORECASE)


def source_tables(sources_dir: Path = SOURCES_DIR) -> set[str]:
    """Every `<schema>.<table>` the source queries read, e.g. `{"marts.dim_country_year", …}`.

    The site's place in the asset graph is decided by this set: the asset declares
    a dep per backing asset, and `tests/test_report.py` fails if a source query
    starts reading a table no dep covers. Without that, adding a source query on a
    new mart would leave the site building from a stale copy of it — with the
    graph still looking correctly ordered.

    Comments are stripped first, or `latest_years.sql`'s coverage table (which
    names columns, not tables) would be parsed as SQL.
    """
    tables = set()
    for query_tables in source_query_tables(sources_dir).values():
        tables |= query_tables
    return tables


def source_query_tables(sources_dir: Path = SOURCES_DIR) -> dict[str, set[str]]:
    """`{"warehouse.emissions_energy": {"marts.fct_emissions_energy"}, …}`.

    Per query rather than per project, which is the resolution `page_tables` needs:
    Evidence pages name *queries*, and only the query knows which warehouse table
    it reads. The key is `<source>.<query>` because that is how a page spells it —
    the source name is the directory under `sources/`.
    """
    per_query = {}
    for sql in sorted(sources_dir.rglob("*.sql")):
        text = _SQL_COMMENT.sub("", sql.read_text())
        name = f"{sql.parent.name}.{sql.stem}"
        per_query[name] = {match.group(1).lower() for match in _TABLE_REF.finditer(text)}
    return per_query


def page_tables(
    pages_dir: Path = PAGES_DIR, sources_dir: Path = SOURCES_DIR
) -> dict[str, set[str]]:
    """`{"retail": {"marts.fct_retail_order_line", …}, …}` — warehouse tables per page.

    Two hops: a page's SQL blocks read `warehouse.<query>`, and the query reads the
    warehouse. This is what the exposures in `dbt/models/_exposures.yml` are checked
    against, so that `dbt ls --select +exposure:retail_dashboard` answers "what
    breaks if I change this model" for one page rather than for the whole site.

    A page naming a query that doesn't exist raises: Evidence fails that build
    anyway, and getting the error here means `just test` catches it without Node.
    """
    queries = source_query_tables(sources_dir)
    sources = {name.split(".", 1)[0] for name in queries}
    tables = {}
    for page in sorted(page_routes(pages_dir, BUILD_DIR)):
        text = _SQL_COMMENT.sub("", (pages_dir / f"{page}.md").read_text())
        referenced = {
            match.group(1).lower()
            for match in _QUERY_REF.finditer(text)
            if match.group(1).split(".", 1)[0].lower() in sources
        }
        unknown = referenced - set(queries)
        if unknown:
            raise ValueError(f"{page}.md reads undefined source queries: {sorted(unknown)}")
        tables[page] = set().union(set(), *(queries[query] for query in referenced))
    return tables


def page_routes(pages_dir: Path = PAGES_DIR, build_dir: Path = BUILD_DIR) -> dict[str, Path]:
    """`{"index": build/index.html, "findings": build/findings/index.html, …}`.

    The asset check reads this: `evidence build` exits 0 whether or not it emitted
    a page for every markdown file, so "the build succeeded" is not the same claim
    as "the site has a page for every file under `pages/`".
    """
    routes = {}
    for page in sorted(pages_dir.rglob("*.md")):
        slug = page.relative_to(pages_dir).with_suffix("").as_posix()
        parent = build_dir if slug == "index" else build_dir / slug
        routes[slug] = parent / "index.html"
    return routes


def _npm(*args: str, cwd: Path = REPORTS_DIR) -> None:
    """Run npm, letting its output through to the caller's stdout (which is what
    Dagster captures for the step). `shutil.which` first, because the failure
    mode otherwise is a bare `FileNotFoundError: 'npm'` several frames deep."""
    if not shutil.which("npm"):
        raise RuntimeError(
            "npm is not on PATH. The Evidence site needs Node >= 18; "
            "every other layer of this pipeline is pure Python."
        )
    subprocess.run(["npm", *args], cwd=cwd, check=True)


def _install(reports_dir: Path) -> str:
    """`npm ci` on a cold checkout, `npm install` on a warm one.

    `ci` installs exactly the lockfile and is what a build should use, but it
    deletes `node_modules` first — so using it unconditionally would re-download
    the tree on every local materialisation. `install` is the one that reconciles
    a `package.json` change, which is the only thing that moves in a warm
    checkout.
    """
    cold = not (reports_dir / "node_modules").exists()
    command = "ci" if cold and (reports_dir / "package-lock.json").exists() else "install"
    _npm(command, cwd=reports_dir)
    return command


def _tree_size(root: Path) -> tuple[int, int]:
    files = [p for p in root.rglob("*") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


def run(
    reports_dir: Path | str = REPORTS_DIR,
    *,
    install: bool = True,
    clean: bool = False,
    strict: bool = True,
) -> dict:
    """Build the static site. Returns a summary used as Dagster asset metadata.

    `install=False` skips npm entirely for a rebuild in a warm checkout; `clean`
    drops the schema cache and the previous output first.
    """
    reports_dir = Path(reports_dir)
    build_dir = reports_dir / "build"

    # Always rewrite `build/` from empty. `evidence build` adds to the directory
    # rather than replacing it — three builds of this site left 139 orphaned
    # `_app/immutable/` chunks behind — so without this the reported file count
    # and size creep upward every run, and a page that was renamed or deleted
    # keeps serving from its old route. Same lesson as the lake's `overwrite true`.
    if build_dir.exists():
        shutil.rmtree(build_dir)
    # `.evidence/` is the expensive one (it holds the extracted parquet), so it
    # only goes when asked.
    if clean and (reports_dir / ".evidence").exists():
        shutil.rmtree(reports_dir / ".evidence")

    if install:
        _install(reports_dir)
    # Extract first: `build` renders whatever parquet is already there, so the
    # order is load-bearing rather than conventional.
    _npm("run", "sources:strict" if strict else "sources", cwd=reports_dir)
    _npm("run", "build", cwd=reports_dir)

    routes = page_routes(reports_dir / "pages", build_dir)
    files, size = _tree_size(build_dir)
    return {
        "pages": len(routes),
        "missing_pages": sorted(slug for slug, path in routes.items() if not path.exists()),
        "source_queries": len(list((reports_dir / "sources").rglob("*.sql"))),
        "warehouse_tables": sorted(source_tables(reports_dir / "sources")),
        "files": files,
        "bytes": size,
        "build_dir": str(build_dir),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--clean",
        action="store_true",
        help="drop .evidence/ and build/ first (needed when a source's columns changed)",
    )
    parser.add_argument(
        "--no-install", dest="install", action="store_false", help="skip npm install/ci"
    )
    parser.add_argument(
        "--no-strict",
        dest="strict",
        action="store_false",
        help="let an empty warehouse build an empty site instead of failing",
    )
    args = parser.parse_args()

    summary = run(install=args.install, clean=args.clean, strict=args.strict)
    print(
        f"{summary['build_dir']}: {summary['pages']} pages, "
        f"{summary['files']:,} files ({summary['bytes'] / 1e6:.1f} MB)"
    )
    if summary["missing_pages"]:
        print(f"  WARNING: no output for {', '.join(summary['missing_pages'])}")


if __name__ == "__main__":
    main()
