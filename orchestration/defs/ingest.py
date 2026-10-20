"""Layer 1 — dlt ingestion into the DuckLake landing zone, and its snapshot expiry."""

# No `from __future__ import annotations`: Dagster refuses a string `context` annotation.

from datetime import UTC, datetime

import dagster as dg
from dagster import AssetExecutionContext
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
from ingest.sources.worldbank import WDI_FIRST_YEAR
from lake.lakehouse import KEEP_WEATHER_LOADS, expire as expire_lakehouse
from orchestration.freshness import RAW_FRESHNESS

RAW_DESCRIPTIONS = {
    "owid_co2": "Our World in Data CO2 & GHG emissions, country-year (CSV).",
    "owid_energy": "Our World in Data energy production/consumption, country-year (CSV).",
    "wb_country": "World Bank country dimension: region, income group, capital (JSON API).",
    "wb_wdi": (
        "World Bank WDI indicators, long-format (JSON API, paginated). Loaded "
        "incrementally: `merge` on (indicator, country_code, year) over a "
        "5-year lookback window, not a full reload."
    ),
    "eu_elec_prices": "Eurostat nrg_pc_204 household electricity prices for 41 countries, the EU-27 among them (JSON-stat).",
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
        "Open-Meteo reanalysis weather, daily, at the capital of each country "
        "Eurostat prices, at (country_iso3, weather_date) — the one source joined on a *coordinate*, "
        "the World Bank's capital latitude/longitude. Loaded "
        "incrementally: `merge` over a 90-day lookback (the preliminary runs are "
        "superseded by final ones months later), backfilled by year range, and paced against a "
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
    for names, kwargs in load_groups(selected, partial=years is not None):
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

    for names, kwargs in load_groups(PARTITIONED_RESOURCES, partial=months is not None):
        yield from dlt.run(
            context=context,
            dlt_source=public_indicators(retail_months=months).with_resources(*names),
            **kwargs,
        )


@dg.asset(
    key=dg.AssetKey(["lake", "snapshot_expiry"]),
    # After the loads in `full_refresh`; retail is not in it, and its old
    # snapshots go at the next run's expiry.
    deps=[*raw_assets.keys, *raw_by_year_assets.keys],
    group_name="raw",
    kinds={"ducklake"},
    description=(
        "Expires lakehouse snapshots before the last "
        f"{KEEP_WEATHER_LOADS} weather loads and deletes the files only they "
        "read. Counted in loads, not days, so `weather_revisions_are_derivable` "
        "keeps its pair on a catalog left idle."
    ),
)
def snapshot_expiry(context: AssetExecutionContext) -> dg.MaterializeResult:
    freed = expire_lakehouse()
    context.log.info("expired %(snapshots)s snapshots, %(files)s files, %(orphans)s orphans", freed)
    return dg.MaterializeResult(metadata=freed)
