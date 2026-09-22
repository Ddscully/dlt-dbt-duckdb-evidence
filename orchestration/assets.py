"""The pipeline as a Dagster asset graph.

    raw/*  (dlt -> DuckLake)  ->  staging/stg_*  ->  marts/fct_*  (dbt)  +->  analytics/co2_intensity  (Polars)
                                                             +->  analytics/retail_rfm     (Polars)
                                                             |      +->  analytics/pipeline_status
                                                             +->  reports/evidence_site  (Evidence)

The layers are wired by *asset key*, not by ordering:

* the dlt resources are keyed ``["raw", <resource>]`` to match the asset keys
  dagster-dbt derives from `dbt/models/staging/_sources.yml`;
* dagster-dbt reads `manifest.json`, so the model-to-model edges come from dbt's
  own `ref()` graph;
* each Polars asset declares the mart it reads;
* the Evidence site declares one dep per table its source queries read, from
  the maps in `publish/build_report.py`.

So changing a `ref()` or adding a source table moves the graph without a shell
script to keep in sync.
"""

# No `from __future__ import annotations`: Dagster refuses a string `context` annotation.

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import dagster as dg
import duckdb
from dagster import AssetExecutionContext
from dagster_dbt import DagsterDbtTranslator, DbtCliResource, dbt_assets, get_asset_key_for_model
from dagster_dlt import DagsterDltResource, DagsterDltTranslator, dlt_assets
from dagster_dlt.translator import DltResourceTranslatorData

from ingest.pipeline import (
    FULL_REFRESH_RESOURCES,
    INCREMENTAL_RESOURCES,
    PARTITIONED_RESOURCES,
    YEAR_RANGE_RESOURCES,
    build_pipeline,
    load_groups,
    public_indicators,
)
from ingest.sources.retail import (
    RETAIL_FIRST_MONTH,
    RETAIL_LAST_MONTH,
)
from ingest.sources.weather import check_weather_range_is_affordable
from ingest.sources.worldbank import (
    WB_WDI_INDICATORS,
    WDI_FIRST_YEAR,
)
from lake.lakehouse import (
    ATTACH_ALIAS,
    LAKEHOUSE_DIR,
    WEATHER_TABLE,
    read_only_connection,
    revisions as weather_revisions,
    rows as weather_rows,
    versions as table_versions_for,
)
from modern_data_stack.db import row, scalar
from modern_data_stack.paths import dbt_run_results_path, dbt_target_path, warehouse_path
from orchestration.resources import dbt_project
from publish.build_report import (
    BUILD_DIR,
    TABLE_TO_ASSET_KEY,
    TABLE_TO_DBT_MODEL,
    page_routes,
    run as build_report,
)
from transform.co2_intensity import run as run_co2_intensity
from transform.pipeline_status import run as run_pipeline_status
from transform.retail_rfm import run as run_retail_rfm

# The warehouse the asset checks read; their tests point it at a throwaway file.
DUCKDB_PATH = warehouse_path()


# --------------------------------------------------------------------------- #
# Freshness policies
# --------------------------------------------------------------------------- #
# A schedule that quietly stopped firing shows as a stale asset, not an absent run.

# Publishers push on their own cadence: two days without a load warns, a week fails.
RAW_FRESHNESS = dg.FreshnessPolicy.time_window(
    fail_window=timedelta(days=7),
    warn_window=timedelta(days=2),
)

# Rebuilt by 08:00 UTC by the daily 06:00 schedule, from data no older than midnight.
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
    "ecb_fx_rates": (
        "ECB daily euro FX reference rates via Frankfurter, at (rate_date, "
        "quote_currency). Loaded incrementally: "
        "`merge` over a 10-day lookback, with no year-range backfill, unlike WDI: "
        "the whole series is one request."
    ),
    "retail_invoice_lines": (
        "UCI Online Retail II — a UK gift retailer's order lines at "
        "(invoice, line_number), 2009-12 to 2011-12. The only source below "
        "country grain and the only bulk file drop: one 45 MB workbook, cached "
        "and then partitioned by invoice month on the *load* rather than the "
        "fetch."
    ),
    "om_weather_daily": (
        "Open-Meteo ERA5 daily weather at each EU/EEA capital city, at "
        "(country_iso3, weather_date) — the one source joined on a *coordinate*, "
        "the World Bank's capital latitude/longitude. Loaded "
        "incrementally: `merge` over a 90-day lookback (ERA5T is superseded by "
        "final ERA5 months later), backfilled by year range, and paced against a "
        "finite API budget rather than fetched whole."
    ),
}

# Three multi-assets, split by how a run can narrow a load: not at all, by a year
# range (run config) or by month (partitions). `load_groups` still owns the
# refresh/merge split; `tests/test_ingest.py` holds the blocks disjoint.
UNWINDOWED_RESOURCES = tuple(
    name
    for name in (*FULL_REFRESH_RESOURCES, *INCREMENTAL_RESOURCES)
    if name not in YEAR_RANGE_RESOURCES and name not in PARTITIONED_RESOURCES
)


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
                # Independent pulls, not dependents of a synthetic source asset.
                deps=[],
            )
        )


class YearRange(dg.Config):
    """The years a WDI or weather run loads instead of its lookback.

    Run config rather than partitions, for the reasons in
    `docs/decisions/0002-yearly-sources-as-run-config.md`. Unset, it loads the
    lookback; a backfill is `just backfill-wdi`, `just backfill-weather`, or this
    config in the Launchpad.
    """

    first_year: int | None = None
    # Unset means `first_year`, so one year needs one field.
    last_year: int | None = None

    def years(self) -> tuple[int, int] | None:
        """The closed range to load, or None for the lookback.

        Bounded where the partitions were: WDI's 1960 floor, and the current
        year, past which the World Bank serves projections `stg_wdi` would have
        to cut again.
        """
        if self.first_year is None:
            if self.last_year is not None:
                raise ValueError("last_year is set without first_year — set both, or neither")
            return None
        last = self.first_year if self.last_year is None else self.last_year
        this_year = datetime.now(UTC).year
        if not WDI_FIRST_YEAR <= self.first_year <= last <= this_year:
            raise ValueError(
                f"years {self.first_year}-{last} are outside {WDI_FIRST_YEAR}-{this_year}, "
                "or run backwards"
            )
        return (self.first_year, last)


@dlt_assets(
    # Mixed dispositions: the body asks `load_groups` for each group's kwargs.
    dlt_source=public_indicators().with_resources(*UNWINDOWED_RESOURCES),
    dlt_pipeline=build_pipeline(),
    dagster_dlt_translator=RawSchemaDltTranslator(),
    name="ingest_public_indicators",
)
def raw_assets(context: AssetExecutionContext, dlt: DagsterDltResource):
    # One op, because the catalog takes a single writer; only what was selected loads.
    selected = {key.path[-1] for key in context.selected_asset_keys}
    for names, kwargs in load_groups(selected):
        context.log.info("loading %s (%s)", ", ".join(names), kwargs)
        yield from dlt.run(
            context=context,
            dlt_source=public_indicators().with_resources(*names),
            **kwargs,
        )


@dlt_assets(
    dlt_source=public_indicators().with_resources(*YEAR_RANGE_RESOURCES),
    dlt_pipeline=build_pipeline(),
    dagster_dlt_translator=RawSchemaDltTranslator(),
    # The justfile's backfill recipes address their config to this name.
    name="ingest_by_year",
)
def raw_by_year_assets(context: AssetExecutionContext, dlt: DagsterDltResource, config: YearRange):
    """The two sources a run can load by year: WDI and capital weather.

    With no config it loads the incremental lookback; with a year range it loads
    exactly those years and leaves the watermarks alone. Weather's year is also
    the unit its API budget is spent in, so its archive cannot be fetched in one
    run.
    """
    years = config.years()
    selected = {key.path[-1] for key in context.selected_asset_keys}

    # The resource refuses too, but inside dlt's extraction error and after the load starts.
    if years is not None and "om_weather_daily" in selected:
        check_weather_range_is_affordable(years)

    window = f"{years[0]}-{years[1]} (backfill)" if years else "incremental lookback"

    # `load_groups` merges these without `refresh`, which would drop their watermarks.
    for names, kwargs in load_groups(selected):
        context.log.info("loading %s over %s", ", ".join(names), window)
        yield from dlt.run(
            context=context,
            dlt_source=public_indicators(wdi_years=years, weather_years=years).with_resources(
                *names
            ),
            **kwargs,
        )


def _month_after(month: str) -> str:
    """The month following `month`, in the same `%Y-%m` form.

    Arithmetic rather than `strptime`: a partition label is not an instant, and
    parsing one raises a timezone question (ruff's DTZ007) with no answer here.
    """
    year, index = (int(part) for part in month.split("-"))
    return f"{year + index // 12}-{index % 12 + 1:02d}"


# Bounded at both ends, because the archive is closed. `end` is exclusive.
RETAIL_PARTITIONS = dg.TimeWindowPartitionsDefinition(
    start=RETAIL_FIRST_MONTH,
    end=_month_after(RETAIL_LAST_MONTH),
    fmt="%Y-%m",
    cron_schedule="0 0 1 * *",
)


@dlt_assets(
    dlt_source=public_indicators().with_resources(*PARTITIONED_RESOURCES),
    dlt_pipeline=build_pipeline(),
    dagster_dlt_translator=RawSchemaDltTranslator(),
    name="ingest_retail",
    partitions_def=RETAIL_PARTITIONS,
    # Every partition reads the same workbook, so one run parses it once.
    backfill_policy=dg.BackfillPolicy.single_run(),
)
def raw_retail_asset(context: AssetExecutionContext, dlt: DagsterDltResource):
    """Retail order lines — partitioned on the *load*, not on the fetch.

    The source is one static workbook, so no request can be narrowed, but a month
    is a re-runnable unit: `invoice_month` comes from the same timestamp as the
    partition key. `load_retail` runs it unpartitioned ahead of every
    `full_refresh`, which must not contain it (`orchestration/definitions.py`).
    """
    months = None
    # An unpartitioned run is legal and `partition_key` raises in it; both tests,
    # because `has_partition_key_range` alone is False for a single partition.
    if context.has_partition_key or context.has_partition_key_range:
        key_range = context.partition_key_range
        months = (key_range.start, key_range.end)
        context.log.info("loading retail_invoice_lines for %s..%s", *months)
    else:
        context.log.info("loading retail_invoice_lines whole (no partition key)")

    for names, kwargs in load_groups(PARTITIONED_RESOURCES):
        yield from dlt.run(
            context=context,
            dlt_source=public_indicators(retail_months=months).with_resources(*names),
            **kwargs,
        )


# --------------------------------------------------------------------------- #
# Layer 2 — dbt staging + marts
# --------------------------------------------------------------------------- #


class FolderGroupDbtTranslator(DagsterDbtTranslator):
    """Group dbt assets by top-level folder, key versioned models under their
    schema, and give every model a freshness policy."""

    def get_asset_key(self, dbt_resource_props: Mapping[str, Any]) -> dg.AssetKey:
        # The default keys a versioned model by `[alias]` alone; prefixing the
        # schema keeps it under `marts/`, where `key:"marts/*"` selects it.
        key = super().get_asset_key(dbt_resource_props)
        if not dbt_resource_props.get("version"):
            return key
        schema = dbt_resource_props.get("config", {}).get("schema")
        return key.with_prefix(schema) if schema else key

    def get_group_name(self, dbt_resource_props: Mapping[str, Any]) -> str | None:
        # Snapshots have no subfolder, and the default names the group after each one.
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
    # `build`, so the tests surface as asset checks. `pipeline_status` reads
    # `run_results.json` from this fixed `target_path`, not a per-run directory.
    yield from dbt.cli(["build"], context=context, target_path=Path(dbt_target_path())).stream()


FCT_EMISSIONS_ENERGY = get_asset_key_for_model([dbt_models], "fct_emissions_energy")
DIM_RETAIL_CUSTOMER = get_asset_key_for_model([dbt_models], "dim_retail_customer")


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
    key=dg.AssetKey(["analytics", "retail_rfm"]),
    deps=[DIM_RETAIL_CUSTOMER],
    group_name="analytics",
    kinds={"polars", "duckdb"},
    freshness_policy=MODELLED_FRESHNESS,
    description=(
        "RFM scores and segments per customer. Quintiles are cut on value "
        "rather than on rank position — SQL's `ntile` would split the 1,626 "
        "one-order customers across two buckets."
    ),
)
def retail_rfm(context: AssetExecutionContext) -> dg.MaterializeResult:
    rows = run_retail_rfm()
    context.log.info("wrote analytics.retail_rfm (%s rows)", rows)
    return dg.MaterializeResult(metadata={"dagster/row_count": rows})


@dg.asset(
    key=dg.AssetKey(["analytics", "pipeline_status"]),
    # Inventories `analytics`, so it follows every table written there.
    deps=[co2_intensity, retail_rfm],
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


WEATHER_RAW = dg.AssetKey(["raw", "om_weather_daily"])


# --------------------------------------------------------------------------- #
# Layer 4 — the Evidence site
# --------------------------------------------------------------------------- #

EVIDENCE_SITE = dg.AssetKey(["reports", "evidence_site"])

# One dep per table the source queries read, so the graph shows what a stale page
# depends on. `publish.build_report` owns the mapping.
SITE_DEPS = [
    *(
        get_asset_key_for_model([dbt_models], model)
        for model in sorted(set(TABLE_TO_DBT_MODEL.values()))
    ),
    # The `pipeline_*` tables share one asset, and Dagster rejects a duplicated dep.
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
        "every page under `reports/pages/` against them. Published by "
        "`.github/workflows/pages.yml`."
    ),
)
def evidence_site(context: AssetExecutionContext) -> dg.MaterializeResult:
    # Needs Node, so `full_refresh` leaves it out.
    summary = build_report()
    context.log.info(
        "built %s pages from %s source queries (%s files, %.1f MB)",
        summary["pages"],
        summary["source_queries"],
        summary["files"],
        summary["bytes"] / 1e6,
    )
    if summary["copied_to_site_root"]:
        context.log.info("copied the site to %s", summary["site_root"])
    return dg.MaterializeResult(
        metadata={
            "pages": summary["pages"],
            "files": summary["files"],
            "bytes": summary["bytes"],
            "warehouse_tables": summary["warehouse_tables"],
            "build_dir": dg.MetadataValue.path(summary["build_dir"]),
            "site_root": dg.MetadataValue.path(summary["site_root"]),
        }
    )


# --------------------------------------------------------------------------- #
# Asset checks — the ones dbt can't express
# --------------------------------------------------------------------------- #


# One more than the carry-forward cap in `dbt_project.yml`, past which today's rates are null.
FX_STALE_AFTER_DAYS = 8


def _scalar(query: str, params: Sequence[Any] | None = None):
    """`db.scalar` against a fresh read-only connection to the warehouse."""
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    try:
        return scalar(con, query, params)
    finally:
        con.close()


@dg.asset_check(asset=dg.AssetKey(["raw", "wb_wdi"]), blocking=True)
def wdi_indicators_all_present() -> dg.AssetCheckResult:
    """Every configured indicator landed at least one row `stg_wdi` keeps.

    Counted through `stg_wdi`'s own filter, because the World Bank has served two
    200 responses that land raw rows and still empty the column: an empty series
    for a bad code, and a stale cached copy with every `countryiso3code` empty.
    Reads the lakehouse: on a fresh checkout the warehouse does not exist yet.
    """
    con = read_only_connection(LAKEHOUSE_DIR)
    try:
        found = {
            r[0]
            for r in con.sql(
                f"""
                select distinct indicator from {ATTACH_ALIAS}.raw.wb_wdi
                where length(country_iso3) = 3
                    and year <= extract(year from current_date)
                    and value is not null
                """
            ).fetchall()
        }
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

    Per column, one per source, because on a country-year spine a bare
    `max(year)` reports the source furthest ahead and hides the one that stalled.
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
        max_years = row(con, f"select {selects} from marts.fct_emissions_energy")
    finally:
        con.close()

    current_year = datetime.now(UTC).year
    # No max year means no rows at all: the worst case, not a pass.
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


FCT_FX_RATES_DAILY = get_asset_key_for_model([dbt_models], "fct_fx_rates_daily")


@dg.asset_check(asset=FCT_FX_RATES_DAILY)
def fx_rates_reach_the_present() -> dg.AssetCheckResult:
    """The newest fixing is within the carry-forward window of today.

    A warning, not a blocker: the ECB publishes nothing over its Christmas closing.
    """
    newest = _scalar("select max(rate_source_date) from marts.fct_fx_rates_daily")
    lag_days = (datetime.now(UTC).date() - newest).days if newest is not None else None
    return dg.AssetCheckResult(
        passed=lag_days is not None and lag_days <= FX_STALE_AFTER_DAYS,
        severity=dg.AssetCheckSeverity.WARN,
        metadata={
            "newest_fixing": str(newest),
            "days_behind": lag_days,
            "threshold_days": FX_STALE_AFTER_DAYS,
        },
    )


@dg.asset_check(asset=co2_intensity, blocking=True)
def co2_intensity_rank_is_dense() -> dg.AssetCheckResult:
    """Each (income_group, year) cohort ranks from 1 with no gaps, and there is at
    least one cohort: an empty table has none to fail, and reaches Evidence unreadable."""
    rows = _scalar("select count(*) from analytics.co2_intensity")
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
    return dg.AssetCheckResult(
        passed=rows > 0 and bad == 0, metadata={"rows": rows, "bad_cohorts": bad}
    )


@dg.asset_check(asset=retail_rfm, blocking=True)
def rfm_scores_do_not_split_ties() -> dg.AssetCheckResult:
    """Two customers with the same value score the same, on all three axes.

    The property the module exists to hold: `ntile(5)` splits runs of equal values
    while still producing a plausible segment mix.
    """
    bad = _scalar(
        """
        select coalesce(sum(n), 0) from (
            select count(*) as n from analytics.retail_rfm
            group by frequency having count(distinct frequency_score) > 1
            union all
            select count(*) from analytics.retail_rfm
            group by recency_days having count(distinct recency_score) > 1
            union all
            select count(*) from analytics.retail_rfm
            group by monetary_gbp having count(distinct monetary_score) > 1
        )
        """
    )
    unsegmented = _scalar("select count(*) from analytics.retail_rfm where segment is null")
    # Only monetary may be null (no revenue line), and `rfm_cell`/`rfm_total` exactly with it.
    unscored = _scalar(
        """
        select count(*) from analytics.retail_rfm
        where recency_score is null
           or frequency_score is null
           or (monetary_gbp is null) <> (monetary_score is null)
           or (monetary_score is null) <> (rfm_cell is null)
           or (monetary_score is null) <> (rfm_total is null)
        """
    )
    return dg.AssetCheckResult(
        passed=bad == 0 and unsegmented == 0 and unscored == 0,
        metadata={
            "customers_scored_against_a_peer": bad,
            "unsegmented": unsegmented,
            "scores_null_where_they_should_not_be": unscored,
        },
    )


@dg.asset_check(asset=pipeline_status, blocking=True)
def run_history_records_this_build() -> dg.AssetCheckResult:
    """The dbt build that just ran left rows in `analytics.pipeline_runs`.

    A wiring check: it looks for this invocation's id, because `count(*) > 0`
    passes on earlier builds' rows while this one appends nothing.
    """
    path = Path(dbt_run_results_path())
    if not path.exists():
        return dg.AssetCheckResult(
            passed=False,
            metadata={
                "run_results_path": str(path),
                "reason": "no dbt run_results.json here — nothing could have been appended",
            },
        )
    invocation = (json.loads(path.read_text()).get("metadata") or {}).get("invocation_id")
    recorded = _scalar(
        "select count(*) from analytics.pipeline_runs where invocation_id = ?",
        [invocation],
    )
    return dg.AssetCheckResult(
        passed=recorded > 0,
        metadata={
            "invocation_id": invocation or "",
            "nodes_recorded": recorded,
            "invocations_in_history": _scalar(
                "select count(distinct invocation_id) from analytics.pipeline_runs"
            ),
        },
    )


@dg.asset_check(asset=WEATHER_RAW, blocking=False)
def weather_revisions_are_derivable() -> dg.AssetCheckResult:
    """The weather restatement log can still be computed from the catalog.

    `lake.lakehouse.revisions()` fails by reporting every row as revised, not by
    erroring. Non-blocking: a first load, which is every CI run, has one version.
    """
    versions = table_versions_for(WEATHER_TABLE, LAKEHOUSE_DIR)
    if len(versions) < 2:
        return dg.AssetCheckResult(
            passed=True,
            metadata={"versions": len(versions), "note": "first load — nothing to diff yet"},
        )

    since, until = versions[-2], versions[-1]
    revised = weather_revisions(WEATHER_TABLE, since, until, LAKEHOUSE_DIR)
    total = weather_rows(WEATHER_TABLE, LAKEHOUSE_DIR)
    # Upstream cannot restate the whole archive between two loads.
    suspect = total > 0 and len(revised) == total
    return dg.AssetCheckResult(
        passed=not suspect,
        metadata={
            "compared": f"{since} -> {until}",
            "rows_revised": len(revised),
            "rows_total": total,
        },
    )


@dg.asset_check(asset=EVIDENCE_SITE, blocking=True)
def site_pages_all_rendered() -> dg.AssetCheckResult:
    """Every page in `reports/pages/` has HTML in `reports/build/`.

    `evidence build` exits 0 for a site missing a page, and a route that
    rendered only the shell looks most like success, so size is checked too.
    """
    routes = page_routes()
    # Real pages render at over 20 kB; the bare SvelteKit shell is under 8 kB.
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
    "FCT_FX_RATES_DAILY",
    "co2_intensity",
    "dbt_models",
    "evidence_site",
    "pipeline_status",
    "raw_assets",
    "raw_by_year_assets",
    "raw_retail_asset",
    "retail_rfm",
]
