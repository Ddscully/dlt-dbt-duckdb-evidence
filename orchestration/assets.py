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

# NB: no `from __future__ import annotations` here — Dagster inspects the
# `context` parameter's annotation object, and a stringified one fails its check.

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
    build_pipeline,
    load_groups,
    public_indicators,
)
from ingest.sources.retail import (
    RETAIL_FIRST_MONTH,
    RETAIL_LAST_MONTH,
)
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
from modern_data_stack.paths import dbt_run_results_path, dbt_target_path
from orchestration.resources import dbt_project
from publish.build_report import (
    BUILD_DIR,
    TABLE_TO_ASSET_KEY,
    TABLE_TO_DBT_MODEL,
    page_routes,
    run as build_report,
)
from transform.co2_intensity import DUCKDB_PATH, run as run_co2_intensity
from transform.pipeline_status import run as run_pipeline_status
from transform.retail_rfm import run as run_retail_rfm

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
    "ecb_fx_rates": (
        "ECB daily euro FX reference rates via Frankfurter, at (rate_date, "
        "quote_currency). Loaded incrementally: "
        "`merge` over a 10-day lookback, and *not* partitioned, unlike WDI."
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
        "final ERA5 months later), year-partitioned, and paced against a finite "
        "API budget rather than fetched whole."
    ),
}

# The eight resources in three multi-assets, split by partition grain because
# Dagster gives every asset in a multi-asset one `partitions_def`: year (WDI,
# weather), month (retail), none (the rest). The two yearly resources share a
# block — separate blocks would mean separate definitions, which `full_refresh`
# cannot resolve (see YEARLY_PARTITIONS). `load_groups` still owns the
# refresh/merge split within each block.
#
# The tuples must cover the source disjointly: a resource in two blocks loads
# twice, one in none leaves the graph. `tests/test_ingest.py` asserts it against
# PARTITIONED_RESOURCES, so the guard runs without the optional orchestration
# dependency group.
YEAR_PARTITIONED_RESOURCES = ("wb_wdi", "om_weather_daily")
MONTH_PARTITIONED_RESOURCES = ("retail_invoice_lines",)
UNPARTITIONED_RESOURCES = (
    *FULL_REFRESH_RESOURCES,
    *(name for name in INCREMENTAL_RESOURCES if name not in PARTITIONED_RESOURCES),
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
                # dlt resources are independent HTTP pulls; the default
                # translator would make them depend on a synthetic source asset.
                deps=[],
            )
        )


# One definition for both yearly resources, and it has to be one: `full_refresh`
# contains both, and `define_asset_job` resolves a selection to a single
# `partitions_def` or raises. The shared start is WDI's 1960, so ERA5's
# 1940-1959 is not addressable; starting at 1940 instead would add twenty WDI
# partitions that can never load anything.
YEARLY_PARTITIONS = dg.TimeWindowPartitionsDefinition(
    start=str(WDI_FIRST_YEAR),
    fmt="%Y",
    cron_schedule="0 0 1 1 *",
    # A yearly window closes on 1 January, so without the offset the current
    # year — the one anybody wants to re-run — would not be a partition.
    end_offset=1,
)


@dlt_assets(
    # Everything that isn't year-partitioned: the four `replace` resources plus
    # `ecb_fx_rates`, which merges but has no per-year fetch to express. The
    # mixed dispositions are fine here because the body asks `load_groups` for
    # the kwargs rather than spelling them.
    dlt_source=public_indicators().with_resources(*UNPARTITIONED_RESOURCES),
    dlt_pipeline=build_pipeline(),
    dagster_dlt_translator=RawSchemaDltTranslator(),
    name="ingest_public_indicators",
)
def raw_assets(context: AssetExecutionContext, dlt: DagsterDltResource):
    # One op for all five: the catalog takes a single writer, so parallel steps
    # would only contend for it. `load_groups` supplies the `run()` kwargs, as it
    # does for the CLI, and takes the selection so one asset means one load.
    selected = {key.path[-1] for key in context.selected_asset_keys}
    for names, kwargs in load_groups(selected):
        context.log.info("loading %s (%s)", ", ".join(names), kwargs)
        yield from dlt.run(
            context=context,
            dlt_source=public_indicators().with_resources(*names),
            **kwargs,
        )


@dlt_assets(
    dlt_source=public_indicators().with_resources(*YEAR_PARTITIONED_RESOURCES),
    dlt_pipeline=build_pipeline(),
    dagster_dlt_translator=RawSchemaDltTranslator(),
    name="ingest_year_partitioned",
    partitions_def=YEARLY_PARTITIONS,
    # One run per range, not per year: the World Bank takes `&date=lo:hi`, so a
    # 30-year WDI backfill is one request per indicator rather than 30. Weather
    # chunks by year inside the resource either way, to pace its budget.
    backfill_policy=dg.BackfillPolicy.single_run(),
)
def raw_year_partitioned_assets(context: AssetExecutionContext, dlt: DagsterDltResource):
    """The two sources with a per-year fetch: WDI and capital weather.

    The other five are not partitioned. Four are whole-file `replace` loads with
    no way to ask for one year; `ecb_fx_rates` merges and takes a date range, but
    its whole series is a single short request, so partitions would buy nothing.
    Weather's year is also the unit its API budget is spent in: the full archive
    costs more than a day's allowance, so it cannot be fetched in one run.

    With a partition key or range (a backfill, `just backfill-wdi`,
    `just backfill-weather`) it loads exactly those years and leaves the
    watermark alone. Without one — `full_refresh`, which the schedule and three
    workflows run — it loads the incremental lookback. Dagster does not reject a
    partitioned asset in an unpartitioned run; the body fails when it touches
    `context.partition_key`. The guard below is what keeps that path working.
    """
    years = None
    # Both properties: `has_partition_key_range` is False for a single-partition
    # run, so testing it alone would load the lookback window for
    # `--partition 1995` and succeed. `partition_key_range` covers both cases.
    if context.has_partition_key or context.has_partition_key_range:
        key_range = context.partition_key_range
        years = (int(key_range.start), int(key_range.end))

    # Only what was selected: materialising `raw/om_weather_daily` alone must not
    # re-fetch WDI.
    selected = {key.path[-1] for key in context.selected_asset_keys}
    window = f"{years[0]}-{years[1]} (partition backfill)" if years else "incremental lookback"

    # Through `load_groups`, which loads these without `refresh` — a refresh
    # would drop their tables and watermarks.
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


# Monthly and bounded at both ends: the archive closed at 2011-12-09, so there
# is no `end_offset`. `end` is exclusive, hence the month *after*
# RETAIL_LAST_MONTH — passing that month itself silently leaves December 2011
# with no partition. `tests/test_definitions.py` pins both ends.
RETAIL_PARTITIONS = dg.TimeWindowPartitionsDefinition(
    start=RETAIL_FIRST_MONTH,
    end=_month_after(RETAIL_LAST_MONTH),
    fmt="%Y-%m",
    cron_schedule="0 0 1 * *",
)


@dlt_assets(
    dlt_source=public_indicators().with_resources(*MONTH_PARTITIONED_RESOURCES),
    dlt_pipeline=build_pipeline(),
    dagster_dlt_translator=RawSchemaDltTranslator(),
    name="ingest_retail",
    partitions_def=RETAIL_PARTITIONS,
    # One run per range: every partition reads the same 45 MB workbook, so
    # per-partition runs would parse it once per month.
    backfill_policy=dg.BackfillPolicy.single_run(),
)
def raw_retail_asset(context: AssetExecutionContext, dlt: DagsterDltResource):
    """Retail order lines — partitioned on the *load*, not on the fetch.

    The source is one static workbook, so no request can be narrowed. A month is
    still a re-runnable unit of work: converting it is real work, the cached
    download makes every partition one fetch, and `invoice_month` comes from the
    same timestamp as the partition key, so re-running a month replaces exactly
    that month.

    Monthly where WDI is yearly, so `full_refresh` cannot contain it;
    `load_retail` runs it unpartitioned ahead of every `full_refresh` (see
    `orchestration/definitions.py`). The partition guard is WDI's, for WDI's
    reason.
    """
    months = None
    if context.has_partition_key or context.has_partition_key_range:
        key_range = context.partition_key_range
        months = (key_range.start, key_range.end)
        context.log.info("loading retail_invoice_lines for %s..%s", *months)
    else:
        context.log.info("loading retail_invoice_lines whole (no partition key)")

    for names, kwargs in load_groups(MONTH_PARTITIONED_RESOURCES):
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
        # The default keys an unversioned model `[schema, name]` but a versioned
        # one `[alias]` alone (`fct_emissions_energy`, `fct_emissions_energy_v1`).
        # Prefixing the schema keeps `marts/...` keys, the `key:"marts/*"`
        # selection and the materialisation history unchanged by versioning.
        key = super().get_asset_key(dbt_resource_props)
        if not dbt_resource_props.get("version"):
            return key
        schema = dbt_resource_props.get("config", {}).get("schema")
        return key.with_prefix(schema) if schema else key

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
    # `build` runs the tests too, so they surface as asset checks on their models.
    # `target_path` is required: by default dagster-dbt writes each invocation's
    # artifacts to a unique subdirectory, and `pipeline_status` reads
    # `run_results.json` from this fixed path — without it `analytics.pipeline_runs`
    # gets no rows. `run_history_records_this_build` guards it.
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
    # Inventories `analytics`, so it follows both tables written there; naming
    # one would let the other land after the count.
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

# One dep per table the source queries read, not one edge to order it last, so
# the graph shows which models a stale page depends on. `publish.build_report`
# owns the mapping and `tests/test_report.py` holds it to the SQL.
SITE_DEPS = [
    *(
        get_asset_key_for_model([dbt_models], model)
        for model in sorted(set(TABLE_TO_DBT_MODEL.values()))
    ),
    # dict.fromkeys: the four `pipeline_*` tables share one asset, and Dagster
    # rejects a duplicated dep.
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


# How far behind today the newest FX fixing may fall before the daily series is
# reported as stale. One more than the carry-forward cap in `dbt_project.yml`:
# inside the cap the dense table still answers with a carried rate, past it every
# row for today is null and a conversion quietly stops returning numbers.
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
    """Every configured indicator landed at least one row.

    The World Bank answers a bad indicator code with a 200 and an empty series,
    which would otherwise become an all-null column in `stg_wdi`.

    Reads the lakehouse, where dlt lands `raw`. The warehouse file holds only
    what dbt builds — and on a fresh checkout does not exist yet when this runs.
    """
    con = read_only_connection(LAKEHOUSE_DIR)
    try:
        found = {
            r[0]
            for r in con.sql(f"select distinct indicator from {ATTACH_ALIAS}.raw.wb_wdi").fetchall()
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
        max_years = row(con, f"select {selects} from marts.fct_emissions_energy")
    finally:
        con.close()

    current_year = datetime.now(UTC).year
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


FCT_FX_RATES_DAILY = get_asset_key_for_model([dbt_models], "fct_fx_rates_daily")


@dg.asset_check(asset=FCT_FX_RATES_DAILY)
def fx_rates_reach_the_present() -> dg.AssetCheckResult:
    """The newest fixing is within the carry-forward window of today.

    `mart_covers_recent_years` measures staleness in years, which cannot see a
    daily series stop. Past the carry-forward cap every dense row for today is
    null, so conversions silently stop producing numbers.

    A warning, not a blocker: the ECB publishes no fixing over the Christmas
    closing days, and a run inside them is not a broken pipeline.
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


@dg.asset_check(asset=retail_rfm, blocking=True)
def rfm_scores_do_not_split_ties() -> dg.AssetCheckResult:
    """Two customers with the same value score the same, on all three axes.

    The property the module exists to hold. `ntile(5)` fills equal-sized buckets
    and so splits runs of equal values — 3,227 of the 5,881 customers share a
    frequency with someone `ntile` would put in another quintile — while still
    producing a plausible segment mix. Counting values with more than one score
    catches a regression to it.
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
    # Every customer has ordered, so a null recency or frequency score is a
    # scoring failure. Monetary is legitimately null for the 28 customers with no
    # revenue line, and `rfm_cell`/`rfm_total` must be null exactly where it is.
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

    A wiring check, not a data check. `build_runs` reads `run_results.json` by
    path, and when that path and the build's target path disagreed every
    orchestrated run wrote the table empty with nothing failing. `count(*) > 0`
    would pass on that state, because earlier builds' rows are still there, so
    this asserts the invocation `run_results.json` names is in the table. A stale
    artifact from an earlier build passes, correctly: its rows were appended then.
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

    dlt rewrites `_dlt_id` and `_dlt_load_id` on every row it re-merges, so
    DuckLake's change feed cannot tell a no-op reload from a restatement.
    `lake.lakehouse.revisions()` diffs two snapshots with those columns
    projected away instead, which depends on the catalog keeping more than one
    version of the table, `at (version => …)` staying valid for the older one,
    and `DLT_COLUMNS` naming every column dlt regenerates. If any slips, the diff
    does not error — it reports every row as revised.

    Non-blocking: a single-version catalog is the honest state of a first load,
    which is every CI run.
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
    # Every row "revised" is the provenance-column failure, not a restatement:
    # upstream cannot restate an entire ERA5 archive between two loads.
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

    `evidence build` exits 0 for a site that is missing a page, and nothing
    downstream reads the output — so without this a half-rendered dashboard would
    materialise green and deploy. Checks the file is non-trivial as well as
    present: a route that rendered nothing but the shell is the failure that looks
    most like success.
    """
    routes = page_routes()
    # Real pages render at over 20 kB; 8 kB catches a route that emitted only
    # the SvelteKit shell.
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
    "raw_retail_asset",
    "raw_year_partitioned_assets",
    "retail_rfm",
]
