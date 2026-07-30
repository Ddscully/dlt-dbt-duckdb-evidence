"""The pipeline as a Dagster asset graph.

    raw/*  (dlt)  ->  staging/stg_*  ->  marts/fct_*  (dbt)  +->  analytics/co2_intensity  (Polars)
                                                             |      +->  analytics/pipeline_status
                                                             +->  lake/parquet_archive  (Parquet)
                                                             +->  reports/evidence_site  (Evidence)

The layers are wired by *asset key*, not by ordering:

* the dlt resources are keyed ``["raw", <resource>]`` to match the asset keys
  dagster-dbt derives from `dbt/models/staging/_sources.yml`;
* dagster-dbt reads `manifest.json`, so the model-to-model edges come from dbt's
  own `ref()` graph;
* the Polars asset declares `deps=[marts/fct_emissions_energy]`;
* the Evidence site declares one dep per table its source queries actually read,
  from the maps in `scripts/build_report.py`.

The upshot: changing a `ref()` or adding a source table moves the graph, and
there is no shell script to keep in sync.
"""

# NB: no `from __future__ import annotations` here — Dagster inspects the
# `context` parameter's annotation object, and a stringified one fails its check.

from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

import dagster as dg
import duckdb
from dagster import AssetExecutionContext
from dagster_dbt import DagsterDbtTranslator, DbtCliResource, dbt_assets, get_asset_key_for_model
from dagster_dlt import DagsterDltResource, DagsterDltTranslator, dlt_assets
from dagster_dlt.translator import DltResourceTranslatorData

from ingest.pipeline import WB_WDI_INDICATORS, build_pipeline, load_groups, public_indicators
from lake.archive import ARCHIVED_TABLES, LAKE_DIR, run as write_lake, table_dir
from orchestration.resources import dbt_project
from scripts.build_report import (
    TABLE_TO_ASSET_KEY,
    TABLE_TO_DBT_MODEL,
    BUILD_DIR,
    page_routes,
    run as build_report,
)
from transform.co2_intensity import DUCKDB_PATH, run as run_co2_intensity
from transform.pipeline_status import run as run_pipeline_status

# --------------------------------------------------------------------------- #
# Freshness policies
# --------------------------------------------------------------------------- #
# These state what *should* be true regardless of whether a run happened, so a
# schedule that quietly stopped firing shows up as a stale asset in the UI
# instead of having to be inferred from an absent run.

# Raw pulls: upstream publishers push on their own cadence, so two days without
# a successful load is worth a warning and a week is a failure.
RAW_FRESHNESS = dg.FreshnessPolicy.time_window(
    fail_window=timedelta(days=7),
    warn_window=timedelta(days=2),
)

# Modelled layers hang off the daily 06:00 schedule: rebuilt by 08:00 UTC from
# data no older than the preceding midnight.
MODELLED_FRESHNESS = dg.FreshnessPolicy.cron(
    deadline_cron="0 8 * * *",
    lower_bound_delta=timedelta(hours=8),
)


# --------------------------------------------------------------------------- #
# Layer 1 — dlt ingestion
# --------------------------------------------------------------------------- #

RAW_DESCRIPTIONS = {
    "owid_co2": "Our World in Data CO2 & GHG emissions, country-year (CSV).",
    "owid_energy": "Our World in Data energy production/consumption, country-year (CSV).",
    "wb_country": "World Bank country dimension: region, income group, capital (JSON API).",
    "wb_wdi": (
        "World Bank WDI indicators, long-format (JSON API, paginated). Loaded "
        "incrementally: `merge` on (indicator, country_code, year) over a "
        "5-year lookback window, not a full reload."
    ),
    "eu_elec_prices": "Eurostat nrg_pc_204 household electricity prices, EU/EEA (JSON-stat).",
}


class RawSchemaDltTranslator(DagsterDltTranslator):
    """Key dlt resources as ``raw/<resource>``.

    dagster-dlt would otherwise name them ``dlt_public_indicators_<resource>``,
    which wouldn't line up with the ``raw`` source keys dagster-dbt generates —
    and the two halves of the graph would sit side by side, unconnected.
    """

    def get_asset_spec(self, data: DltResourceTranslatorData) -> dg.AssetSpec:
        name = data.resource.name
        return (
            super()
            .get_asset_spec(data)
            .replace_attributes(
                key=dg.AssetKey(["raw", name]),
                group_name="ingestion",
                description=RAW_DESCRIPTIONS.get(name),
                freshness_policy=RAW_FRESHNESS,
                # dlt resources are independent HTTP pulls; the default
                # translator would make them depend on a synthetic source asset.
                deps=[],
            )
        )


@dlt_assets(
    dlt_source=public_indicators(),
    dlt_pipeline=build_pipeline(),
    dagster_dlt_translator=RawSchemaDltTranslator(),
    name="ingest_public_indicators",
)
def raw_assets(context: AssetExecutionContext, dlt: DagsterDltResource):
    # One op for all five resources: DuckDB takes a single writer, so fanning
    # these out into parallel steps would just make them fight over the file.
    #
    # Two loads inside it, though, because `refresh` is a run-level argument:
    # the replace resources want their schema dropped and re-inferred, and
    # `wb_wdi` merges into what's already there and would lose its incremental
    # watermark to the same drop. `load_groups` is shared with the CLI so both
    # paths split it the same way, and it takes the selection so materialising
    # one asset still runs one load.
    selected = {key.path[-1] for key in context.selected_asset_keys}
    for names, kwargs in load_groups(selected):
        context.log.info("loading %s (%s)", ", ".join(names), kwargs or "incremental")
        yield from dlt.run(
            context=context,
            dlt_source=public_indicators().with_resources(*names),
            **kwargs,
        )


# --------------------------------------------------------------------------- #
# Layer 2 — dbt staging + marts
# --------------------------------------------------------------------------- #


class FolderGroupDbtTranslator(DagsterDbtTranslator):
    """Group dbt assets by their folder (`staging`, `marts`) and give them a freshness policy."""

    def get_group_name(self, dbt_resource_props: Mapping[str, Any]) -> str | None:
        # Snapshots live directly in `snapshots/`, so there's no folder to take —
        # and the default would name the group after the snapshot itself.
        if dbt_resource_props.get("resource_type") == "snapshot":
            return dbt_resource_props.get("schema")
        fqn = dbt_resource_props.get("fqn") or []
        # fqn is [project, <subfolders...>, name]
        return fqn[1] if len(fqn) > 2 else super().get_group_name(dbt_resource_props)

    def get_asset_spec(self, manifest, unique_id, project) -> dg.AssetSpec:
        spec = super().get_asset_spec(manifest, unique_id, project)
        return spec.replace_attributes(freshness_policy=MODELLED_FRESHNESS)


@dbt_assets(
    manifest=dbt_project.manifest_path,
    dagster_dbt_translator=FolderGroupDbtTranslator(),
)
def dbt_models(context: AssetExecutionContext, dbt: DbtCliResource):
    # `build` = run + test, so dbt's schema tests surface as Dagster asset checks
    # on the model they belong to.
    yield from dbt.cli(["build"], context=context).stream()


FCT_EMISSIONS_ENERGY = get_asset_key_for_model([dbt_models], "fct_emissions_energy")


# --------------------------------------------------------------------------- #
# Layer 3 — Polars derived metrics
# --------------------------------------------------------------------------- #


@dg.asset(
    key=dg.AssetKey(["analytics", "co2_intensity"]),
    deps=[FCT_EMISSIONS_ENERGY],
    group_name="analytics",
    kinds={"polars", "duckdb"},
    freshness_policy=MODELLED_FRESHNESS,
    description=(
        "CO2 per $ GDP (derived from World Bank GDP, not OWID's shorter "
        "co2_per_gdp), dense-ranked within each (income group, year) cohort."
    ),
)
def co2_intensity(context: AssetExecutionContext) -> dg.MaterializeResult:
    rows = run_co2_intensity()
    context.log.info("wrote analytics.co2_intensity (%s rows)", rows)
    return dg.MaterializeResult(metadata={"dagster/row_count": rows})


@dg.asset(
    key=dg.AssetKey(["analytics", "pipeline_status"]),
    # Inventories `analytics`, so it has to run after the last thing written
    # there. `co2_intensity` is itself downstream of the mart, which is
    # downstream of every raw table — one edge orders this behind the lot.
    deps=[co2_intensity],
    group_name="analytics",
    kinds={"polars", "duckdb"},
    freshness_policy=MODELLED_FRESHNESS,
    description=(
        "Pipeline observability: dlt load times per source, row counts and year "
        "spans per layer, and the stored-failure count for every dbt test. "
        "Rendered by the Evidence 'Pipeline' page."
    ),
)
def pipeline_status(context: AssetExecutionContext) -> dg.MaterializeResult:
    written = run_pipeline_status()
    for name, rows in written.items():
        context.log.info("wrote analytics.%s (%s rows)", name, rows)
    return dg.MaterializeResult(
        metadata={"dagster/row_count": sum(written.values()), "tables": written}
    )


# --------------------------------------------------------------------------- #
# Layer 4 — the Parquet lake
# --------------------------------------------------------------------------- #


@dg.asset(
    key=dg.AssetKey(["lake", "parquet_archive"]),
    # The mart is downstream of every raw table, so depending on it is enough to
    # order the archive after the whole warehouse.
    deps=[FCT_EMISSIONS_ENERGY],
    group_name="lake",
    kinds={"duckdb", "parquet"},
    freshness_policy=MODELLED_FRESHNESS,
    description=(
        "Year-partitioned Parquet copy of the raw and mart tables under "
        "data/lake/ — partition pruning, portability, and a diff between runs "
        "that shows which years upstream actually changed."
    ),
)
def parquet_archive(context: AssetExecutionContext) -> dg.MaterializeResult:
    summary = write_lake()
    for table, stats in summary.items():
        context.log.info(
            "%s -> %s rows in %s partitions (%.1f MB)",
            table,
            stats["rows"],
            stats["partitions"],
            stats["bytes"] / 1e6,
        )
    return dg.MaterializeResult(
        metadata={
            "dagster/row_count": sum(s["rows"] for s in summary.values()),
            "files": sum(s["files"] for s in summary.values()),
            "bytes": sum(s["bytes"] for s in summary.values()),
            "tables": summary,
        }
    )


# --------------------------------------------------------------------------- #
# Layer 5 — the Evidence site
# --------------------------------------------------------------------------- #

EVIDENCE_SITE = dg.AssetKey(["reports", "evidence_site"])

# One dep per table the source queries read, rather than the single edge that
# would be enough to order this last. The lake gets away with `deps=[the mart]`
# because it archives one table list; the site reads eight tables across two
# layers, and a graph that showed it hanging off only the mart would be wrong
# about what a stale dashboard means. `scripts.build_report` owns the mapping and
# `tests/test_report.py` holds it to the SQL.
SITE_DEPS = [
    *(
        get_asset_key_for_model([dbt_models], model)
        for model in sorted(set(TABLE_TO_DBT_MODEL.values()))
    ),
    # dict.fromkeys: three of the four analytics tables share `pipeline_status`,
    # and Dagster rejects a duplicated dep.
    *(dg.AssetKey(list(key)) for key in dict.fromkeys(TABLE_TO_ASSET_KEY.values())),
]


@dg.asset(
    key=EVIDENCE_SITE,
    deps=SITE_DEPS,
    group_name="reports",
    kinds={"evidence", "duckdb"},
    freshness_policy=MODELLED_FRESHNESS,
    description=(
        "The Evidence dashboard as a static site in `reports/build/`: extracts "
        "the warehouse tables to parquet (`npm run sources:strict`), then renders "
        "the five pages against them. Published by `.github/workflows/pages.yml`."
    ),
)
def evidence_site(context: AssetExecutionContext) -> dg.MaterializeResult:
    # Needs Node on PATH, which is why this asset is *excluded* from the
    # `full_refresh` job — see orchestration/definitions.py.
    summary = build_report()
    context.log.info(
        "built %s pages from %s source queries (%s files, %.1f MB)",
        summary["pages"],
        summary["source_queries"],
        summary["files"],
        summary["bytes"] / 1e6,
    )
    return dg.MaterializeResult(
        metadata={
            "pages": summary["pages"],
            "files": summary["files"],
            "bytes": summary["bytes"],
            "warehouse_tables": summary["warehouse_tables"],
            "build_dir": dg.MetadataValue.path(summary["build_dir"]),
        }
    )


# --------------------------------------------------------------------------- #
# Asset checks — the ones dbt can't express
# --------------------------------------------------------------------------- #


def _scalar(query: str):
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    try:
        return con.sql(query).fetchone()[0]
    finally:
        con.close()


@dg.asset_check(asset=dg.AssetKey(["raw", "wb_wdi"]), blocking=True)
def wdi_indicators_all_present() -> dg.AssetCheckResult:
    """Every configured indicator landed at least one row.

    `_get_json` now raises on a non-2xx, but the World Bank also answers a bad
    indicator code with a 200 and an empty series — which would quietly become
    an all-null column in `stg_wdi` rather than a failure.
    """
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    try:
        found = {r[0] for r in con.sql("select distinct indicator from raw.wb_wdi").fetchall()}
    finally:
        con.close()
    missing = sorted(set(WB_WDI_INDICATORS) - found)
    return dg.AssetCheckResult(
        passed=not missing,
        metadata={"missing_indicators": missing, "indicators_loaded": len(found)},
    )


@dg.asset_check(asset=FCT_EMISSIONS_ENERGY)
def mart_covers_recent_years() -> dg.AssetCheckResult:
    """Every source feeding the mart should reach within two years of today.

    A source that silently stops updating shows up here long before anyone
    notices a dashboard has gone flat. It is measured per column because the
    mart sits on a country-year spine: a bare `max(year)` reports whichever
    source is furthest ahead and hides the one that stalled. One column per
    source is enough — the columns from a given source move together.
    """
    columns = {
        "co2_mt": "owid_co2",
        "primary_energy_twh": "owid_energy",
        "gdp_constant_usd": "wb_wdi",
        "electricity_price_eur_kwh": "eu_elec_prices",
    }
    selects = ", ".join(f"max(year) filter (where {col} is not null)" for col in columns)
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    try:
        max_years = con.sql(f"select {selects} from marts.fct_emissions_energy").fetchone()
    finally:
        con.close()

    current_year = date.today().year
    # a source with no rows at all has no max year — that's the worst case, not a pass
    lags = {
        source: (current_year - year if year is not None else None)
        for source, year in zip(columns.values(), max_years, strict=True)
    }
    return dg.AssetCheckResult(
        passed=all(lag is not None and lag <= 2 for lag in lags.values()),
        severity=dg.AssetCheckSeverity.WARN,
        metadata={
            "max_year_by_source": {
                source: year for source, year in zip(columns.values(), max_years, strict=True)
            },
            "years_behind": lags,
        },
    )


@dg.asset_check(asset=co2_intensity, blocking=True)
def co2_intensity_rank_is_dense() -> dg.AssetCheckResult:
    """Each (income_group, year) cohort ranks from 1 with no gaps."""
    bad = _scalar(
        """
        select count(*) from (
            select income_group, year
            from analytics.co2_intensity
            group by income_group, year
            having min(co2_intensity_rank) <> 1
                or max(co2_intensity_rank) <> count(distinct co2_intensity_rank)
        )
        """
    )
    return dg.AssetCheckResult(passed=bad == 0, metadata={"bad_cohorts": bad})


@dg.asset_check(asset=parquet_archive, blocking=True)
def lake_matches_warehouse() -> dg.AssetCheckResult:
    """Every archived table reads back from Parquet with the row count and year
    span it has in the warehouse.

    The failure mode this catches is a partial write: `COPY … PARTITION_BY` that
    half-succeeded, or a stale partition left behind by an earlier run. Nothing
    downstream reads the lake, so without a check a broken archive would sit
    there looking materialised.
    """
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    try:
        mismatches = {}
        for table in ARCHIVED_TABLES:
            glob = f"{table_dir(LAKE_DIR, table)}/**/*.parquet"
            warehouse = con.sql(f"select count(*), min(year), max(year) from {table}").fetchone()
            archived = con.sql(
                f"""
                select count(*), min(year), max(year)
                from read_parquet('{glob}', hive_partitioning = 1)
                """
            ).fetchone()
            if warehouse != archived:
                mismatches[table] = {"warehouse": list(warehouse), "lake": list(archived)}
    finally:
        con.close()
    return dg.AssetCheckResult(
        passed=not mismatches,
        metadata={"tables_checked": len(ARCHIVED_TABLES), "mismatches": mismatches},
    )


@dg.asset_check(asset=EVIDENCE_SITE, blocking=True)
def site_pages_all_rendered() -> dg.AssetCheckResult:
    """Every page in `reports/pages/` has HTML in `reports/build/`.

    `evidence build` exits 0 for a site that is missing a page, and nothing
    downstream reads the output — so without this a half-rendered dashboard would
    materialise green and deploy. Checks the file is non-trivial as well as
    present: a route that rendered nothing but the shell is the failure that looks
    most like success.
    """
    routes = page_routes()
    # The five pages render at 17-74 kB, so 8 kB is well under anything real
    # while still catching a route that emitted nothing but the SvelteKit shell.
    empty = {
        slug: path.stat().st_size
        for slug, path in routes.items()
        if path.exists() and path.stat().st_size < 8_000
    }
    missing = sorted(slug for slug, path in routes.items() if not path.exists())
    return dg.AssetCheckResult(
        passed=not missing and not empty,
        metadata={
            "pages_expected": len(routes),
            "missing": missing,
            "suspiciously_small": empty,
            "build_dir": dg.MetadataValue.path(str(BUILD_DIR)),
        },
    )


__all__ = [
    "EVIDENCE_SITE",
    "FCT_EMISSIONS_ENERGY",
    "co2_intensity",
    "dbt_models",
    "evidence_site",
    "parquet_archive",
    "pipeline_status",
    "raw_assets",
]
