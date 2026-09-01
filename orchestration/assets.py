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
* the Polars asset declares `deps=[marts/fct_emissions_energy]`;
* the Evidence site declares one dep per table its source queries actually read,
  from the maps in `publish/build_report.py`.

The upshot: changing a `ref()` or adding a source table moves the graph, and
there is no shell script to keep in sync.
"""

# NB: no `from __future__ import annotations` here — Dagster inspects the
# `context` parameter's annotation object, and a stringified one fails its check.

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
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
        "quote_currency) — the one sub-annual source. Loaded incrementally: "
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
        "read off `stg_country`'s capital latitude/longitude. Loaded "
        "incrementally: `merge` over a 90-day lookback (ERA5T is superseded by "
        "final ERA5 months later), year-partitioned, and paced against a finite "
        "API budget rather than fetched whole."
    ),
}

# The blocks below split the eight resources by whether they are partitioned and
# *at what grain*, not by how they load — `load_groups` still owns the
# refresh/merge split, and each selection is passed through it.
#
# There are three blocks rather than two because **Dagster gives every asset in a
# multi-asset the same `partitions_def`**, and the three partitioned resources
# are not all partitioned alike: WDI and capital weather by year (annual series,
# and a yearly slice of an API budget), retail by month (a two-year transaction
# window, where a year would be one partition and a day would be 740 of them for
# a shop that trades ~600). Putting them in one block would force one grain onto
# all three, and the only grain that fits every one is the finest — 66 years of
# empty monthly WDI partitions.
#
# The corollary is that **the split is by grain, not one block per resource**:
# `wb_wdi` and `om_weather_daily` share a block precisely because they share a
# grain, and giving them a block each would give them a `partitions_def` each,
# which is what `full_refresh` cannot resolve. See `YEARLY_PARTITIONS`.
#
# Three disjoint tuples covering the source, so an added resource lands in
# exactly one block: a resource in two would be loaded twice per refresh, and one
# in none would silently vanish from the graph. The covering assertion lives in
# `tests/test_ingest.py`, against `PARTITIONED_RESOURCES` — deliberately over
# there rather than in a test of this module, because `just test` runs without
# the optional `orchestration` dependency group and a Dagster import would make
# the guard skip exactly when it is least likely to be noticed.
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


# Yearly partitions, shared by the two resources that have them — see
# `raw_year_partitioned_assets`. Dagster requires every asset in a multi-asset to
# share one `partitions_def`, which is why these sit in their own block rather
# than beside the unpartitioned five.
#
# **Sharing one definition is required, not merely tidy.** `full_refresh` is
# `AssetSelection.all()` minus two things, so it contains both of these, and
# `define_asset_job` resolves a selection to a *single* `partitions_def` or
# raises — there is no opt-out for a named job. Two separate yearly definitions,
# differing only in their start year, would therefore break the job that three
# workflows execute, at definition time, for no behavioural gain.
#
# The cost of sharing is the start year: 1960 is the World Bank's floor, and
# ERA5 reaches back to 1940. So weather's 1940-1959 is not addressable as a
# partition. That is the right way round — the alternative starts both at 1940
# and creates twenty WDI partitions that load nothing, which this repo already
# treats as a defect rather than a curiosity (see `record_retail`'s monthly
# top-up) — and it is 47 years below where the weather seed actually starts.
YEARLY_PARTITIONS = dg.TimeWindowPartitionsDefinition(
    start=str(WDI_FIRST_YEAR),
    fmt="%Y",
    cron_schedule="0 0 1 1 *",
    # The current year has to be a partition — it's the one anybody wants to
    # re-run. A yearly window only closes on 1 January, so without the offset the
    # newest partition is *last* year and today's data has nowhere to go.
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
    # One op for all five resources: DuckDB takes a single writer, so fanning
    # these out into parallel steps would just make them fight over the file.
    #
    # `load_groups` is still what supplies the `run()` kwargs, rather than
    # spelling `refresh=REFRESH` here — it is shared with the CLI so both paths
    # split the dispositions the same way, and it takes the selection so
    # materialising one asset still runs one load.
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
    # One run per *range*, not per year: the World Bank takes `&date=lo:hi` and
    # Open-Meteo takes `&start_date=…&end_date=…`, so a 30-year backfill is 11
    # requests for WDI (one per indicator) rather than 330, and 30 for weather
    # (one per year) rather than 30 — the same for weather either way, because
    # there the chunking is a *budget* decision made inside the resource.
    backfill_policy=dg.BackfillPolicy.single_run(),
)
def raw_year_partitioned_assets(context: AssetExecutionContext, dlt: DagsterDltResource):
    """The two sources with a per-year fetch to express: WDI and capital weather.

    Partitioning the other five would be a fiction. Four are whole-file
    `replace` loads (two CSVs, a JSON-stat payload and a dimension table) with
    no way to ask for one year. The fifth, `ecb_fx_rates`, *is* incremental and
    *does* take a date range — which is the interesting near-miss: merging is not
    what earns a partition. Its whole 27-year series is one three-second request,
    so partitioning it would trade a single call for thousands of Dagster
    partitions and buy nothing.

    The two here earn it for related but distinct reasons, and the second one
    widens the rule. WDI's API takes a date range **and** its series are large
    enough that a window is worth asking for. `om_weather_daily` takes a range
    too, but what makes a year the right unit there is that the year is what the
    *budget* is spent in: 41 capitals over 2007-2025 costs more of Open-Meteo's
    daily allowance than a day contains, so unlike WDI the full history genuinely
    cannot be fetched in one run however patient the caller is.

    Two paths into the same load, and the difference is only which window is
    asked for:

    * **partitioned** — an explicit year range, which is what a backfill from the
      UI or `just backfill-wdi` / `just backfill-weather` sends. Loads exactly
      those years and leaves the watermark alone.
    * **unpartitioned** — the incremental lookback, i.e. what the daily schedule,
      CI and the release workflow have always done. `full_refresh` contains these
      assets and runs with no partition key, and *that has to keep working*:
      three workflows execute that job on a bare checkout.

    A partitioned asset in an unpartitioned run doesn't fail at plan time — it
    fails inside the body, the first time it touches `context.partition_key`. So
    the fallback is the guard below, not something the job definition provides.
    """
    years = None
    # Both properties, because they are not the same question:
    # `has_partition_key_range` is False for a run targeting a *single*
    # partition, so testing it alone makes `--partition 1995` fall quietly
    # through to the incremental branch — a run that says 1995 and loads the
    # lookback window instead, successfully. `partition_key_range` itself is the
    # one that covers both (start == end for a single key).
    if context.has_partition_key or context.has_partition_key_range:
        key_range = context.partition_key_range
        years = (int(key_range.start), int(key_range.end))

    # Scoped to what was actually selected, which matters now that the block
    # holds two resources: materialising `raw/om_weather_daily` alone must not
    # also re-ask the World Bank for eleven indicators. `raw_assets` has always
    # done this; here it was a no-op with one resource in the tuple and stopped
    # being one the moment a second arrived.
    selected = {key.path[-1] for key in context.selected_asset_keys}
    window = f"{years[0]}-{years[1]} (partition backfill)" if years else "incremental lookback"

    # `load_groups` again rather than a bare `dlt.run(...)`: it is what asserts
    # these resources load *without* `refresh`, which would drop their tables and
    # their watermarks with them.
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

    Arithmetic rather than `strptime`, because a partition label is a label and
    not an instant — parsing one into a datetime asks a timezone question that
    has no answer here, which is what `DTZ007` is pointing at when it fires.
    """
    year, index = (int(part) for part in month.split("-"))
    return f"{year + index // 12}-{index % 12 + 1:02d}"


# Monthly, and bounded at both ends — unlike WDI's, which runs to the current
# year with `end_offset=1`. This source is a closed archive: the last transaction
# is 2011-12-09 and there will never be another, so a partition beyond that would
# be a window nobody can fill. `end_offset` is deliberately absent for the same
# reason it is required there.
#
# **`end` is exclusive**, which is why it is a month past the data rather than
# `RETAIL_LAST_MONTH` itself. Passing the last month directly is the obvious
# thing to write and it silently drops that month: the definition resolved to 24
# keys ending at 2011-11, so the 25,526 lines invoiced in December 2011 had no
# partition to land in and no key that could target them. Nothing failed — the
# unpartitioned path every workflow uses loads the whole file — so only a
# per-partition backfill would ever have shown it, by quietly stopping a month
# early. `tests/test_definitions.py` pins both ends against the constants.
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
    # One run per range, as with WDI, but for the opposite reason. There the win
    # is collapsing 330 HTTP requests into 11. Here there is only ever one file:
    # a 25-partition backfill run per-partition would parse the same 45 MB
    # workbook twenty-five times, which is the cost this policy removes.
    backfill_policy=dg.BackfillPolicy.single_run(),
)
def raw_retail_asset(context: AssetExecutionContext, dlt: DagsterDltResource):
    """Retail order lines — partitioned on the *load*, not on the fetch.

    This is the third answer to "what earns a partition", and the one that says
    what the rule actually is. `wb_wdi` earns one because the API takes
    `&date=lo:hi`. `ecb_fx_rates` is refused one although it merges and takes a
    date range, because its whole series is a single three-second request. This
    source can't narrow its fetch at all — there is one static 45 MB workbook and
    no request to put a window in — and it is partitioned anyway, because reading
    and converting a month *is* real work, the cached download means twenty-five
    partitions are still one download, and `invoice_month` is derived from the
    same timestamp the partition key is, so re-running one month replaces exactly
    that month and nothing else.

    So a partition is earned by being a re-runnable unit of work that maps onto a
    slice of the destination. That it maps onto a slice of the *request* is the
    common case, not the requirement.

    The unpartitioned path has to keep working here for the same reason it does
    for WDI, but through a different job. An asset job takes a *single*
    partitions definition, and this one is monthly where `raw/wb_wdi`'s is
    yearly — so this asset is the one thing `full_refresh` excludes besides the
    site, and `load_retail` runs it with no partition key ahead of every
    `full_refresh` (see `orchestration/definitions.py`). Same guard below, and
    the same reason it tests both properties — `has_partition_key_range` alone is
    False for a single-partition run, which would load the whole workbook while
    reporting one month.
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
    """Group dbt assets by their folder (`staging`, `marts`) and give them a freshness policy."""

    def get_asset_key(self, dbt_resource_props: Mapping[str, Any]) -> dg.AssetKey:
        # Versioning a model changes its asset key, and nothing tells you.
        # `default_asset_key_fn` keys an ordinary model on
        # `[configured_schema, name]` — `marts/fct_emissions_energy` — but a
        # *versioned* one on `[alias]` alone, so adding `versions:` to that model
        # renamed its asset to `fct_emissions_energy` and gave v1 the sibling key
        # `fct_emissions_energy_v1`. Both still run; what breaks is everything
        # that spells the key out. `just materialize-select 'key:"marts/*"'`
        # stops matching either of them, and Dagster's materialization history
        # is keyed on the asset key, so the model appears to have never been
        # built. (Write that selection `marts/*` and it matches *nothing* — a
        # bare prefix is not a glob, so the parser reads `marts/` as a key,
        # finds none, and `*` takes everything downstream of the empty set.)
        #
        # Putting the schema back is a two-line override and keeps the key the
        # same on both sides of the migration, which is the only reason the
        # version is invisible to the rest of the graph.
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
    # `build` = run + test, so dbt's schema tests surface as Dagster asset checks
    # on the model they belong to.
    yield from dbt.cli(["build"], context=context).stream()


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
    # Inventories `analytics`, so it has to run after the last thing written
    # there. Both Polars tables are named, not just the one that happens to be
    # downstream of the most: `co2_intensity` alone would order this behind the
    # country mart and leave `retail_rfm` free to land after the inventory that
    # is supposed to count it.
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


# --------------------------------------------------------------------------- #
# Layer 4 — the Parquet lake
# --------------------------------------------------------------------------- #


# **There is no asset here any more, and that is the shape of the change.**
# This layer used to hold two: `lake/parquet_archive`, which copied the
# warehouse out to hive-partitioned Parquet, and `lake/lakehouse`, which merged
# two weather tables into a DuckLake catalog beside it. dlt now writes DuckLake
# *directly*, so the files this layer used to produce are produced by the
# ingest assets, and an asset that re-copied them would be archiving the
# archive.
#
# What survives is the observability: `just lakehouse` reports the catalog's
# tables and snapshot lineage, and the check below guards the one capability
# that the move actually put at risk.


WEATHER_RAW = dg.AssetKey(["raw", "om_weather_daily"])


# --------------------------------------------------------------------------- #
# Layer 5 — the Evidence site
# --------------------------------------------------------------------------- #

EVIDENCE_SITE = dg.AssetKey(["reports", "evidence_site"])

# One dep per table the source queries read, rather than the single edge that
# would be enough to order this last. The lake gets away with `deps=[the mart]`
# because it archives one table list; the site reads ten tables across two
# layers, and a graph that showed it hanging off only the mart would be wrong
# about what a stale dashboard means. `publish.build_report` owns the mapping and
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


def _scalar(query: str):
    """`db.scalar` against a fresh read-only connection to the warehouse."""
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    try:
        return scalar(con, query)
    finally:
        con.close()


@dg.asset_check(asset=dg.AssetKey(["raw", "wb_wdi"]), blocking=True)
def wdi_indicators_all_present() -> dg.AssetCheckResult:
    """Every configured indicator landed at least one row.

    `_get_json` now raises on a non-2xx, but the World Bank also answers a bad
    indicator code with a 200 and an empty series — which would quietly become
    an all-null column in `stg_wdi` rather than a failure.

    It reads the **lakehouse**, not the warehouse file. dlt lands `raw` in the
    catalog now and `data/warehouse.duckdb` holds only what dbt builds, so the
    old spelling was wrong in both directions at once: on a tree that predates
    the move it passed against the stale pre-migration `raw.wb_wdi` still sitting
    in that file, and on a fresh checkout it raised `Cannot open database … in
    read-only mode` — the file does not exist until dbt has run, and this check
    gates the ingest that comes before it.
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

    Every other source here publishes annually, so `mart_covers_recent_years`
    measures staleness in *years* and would call a rate series that stopped in
    March perfectly healthy. This is the same question at the cadence this source
    actually has: once the newest fixing is older than the carry-forward cap,
    every dense row for today is null rather than stale, and a conversion
    silently stops producing numbers at all.

    A warning rather than a blocker: the ECB closes for up to five consecutive
    days at Christmas, and a run in the middle of one is not a broken pipeline.
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

    This checks the exact property the module exists to hold. The one-line SQL
    version of RFM is `ntile(5) over (order by …)`, which fills equal-sized
    buckets and therefore cuts straight through a run of equal values — 3,227 of
    the 5,881 customers here share a frequency with someone `ntile` would put in
    a different quintile. A regression to that form still produces five tidy
    buckets and a plausible-looking segment mix, so nothing else in the pipeline
    would notice. Counting values that carry more than one score does.
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
    # Recency and frequency are never absent — every customer in the dimension
    # has ordered — so a null score on either is `qcut` having failed, not the
    # data being thin. Monetary is the one axis that legitimately goes null (the
    # 28 customers with no revenue line), and `rfm_cell`/`rfm_total` are null
    # exactly and only where it is. Asserting the *equality* rather than allowing
    # nulls is what keeps this from becoming a licence for them: a null leaking
    # into recency would otherwise arrive as a plausible-looking gap.
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


@dg.asset_check(asset=WEATHER_RAW, blocking=False)
def weather_revisions_are_derivable() -> dg.AssetCheckResult:
    """The restatement log can still be computed from the catalog.

    This replaces two checks that the move deleted, and it guards a narrower
    thing than either of them did — deliberately, because the failures they
    covered are now impossible rather than merely unobserved.
    `lake_matches_warehouse` compared a hand-written Parquet copy against the
    warehouse, and there is no copy any more; `lakehouse_matches_warehouse`
    caught an upsert that succeeded while its prune failed, and dlt performs
    both inside one load package.

    What is *newly* fragile is the substitute for the change feed. dlt rewrites
    `_dlt_id` and `_dlt_load_id` on every row it re-merges, so
    `ducklake_table_changes()` reports a no-op reload and a real restatement
    identically — measured at 500 preimages for 500 unchanged rows. The
    replacement is an `EXCEPT` between two snapshots with those columns
    projected away, and it depends on three things staying true: the catalog
    keeping more than one version of the table, `at (version => …)` remaining
    valid for the oldest of them, and the provenance list still naming every
    column dlt regenerates. If any of those slips the diff does not error — it
    returns *every* row as revised, which reads exactly like a catastrophic
    upstream restatement.

    Non-blocking because a single-version catalog is the honest state of a first
    load, which is what every CI run is.
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
    # The pages render at 17-90 kB, so 8 kB is well under anything real while
    # still catching a route that emitted nothing but the SvelteKit shell.
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
