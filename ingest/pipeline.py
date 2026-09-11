"""dlt ingestion: pull the public sources into the DuckLake landing zone.

Sources (all freely licensed; country + year keyed apart from the last):
  - OWID CO2 & GHG        https://github.com/owid/co2-data
  - OWID Energy           https://github.com/owid/energy-data
  - World Bank WDI        https://databank.worldbank.org/source/world-development-indicators
  - World Bank countries  https://api.worldbank.org/v2/country?format=json  (dimension table)
  - Eurostat prices       https://ec.europa.eu/eurostat/databrowser/view/nrg_pc_204  (EU only)
  - ECB reference rates   https://frankfurter.dev  (daily FX)
  - UCI Online Retail II  https://archive.ics.uci.edu/dataset/502/online+retail+ii
                          (order lines — the one grain below a country, and the
                           one source that is a bulk file drop rather than an API)
  - Open-Meteo ERA5       https://open-meteo.com/en/docs/historical-weather-api
                          (daily capital-city weather — the one source joined on
                           a *coordinate*, and the one with a finite budget)

Set ``INGEST_FIXTURES=1`` to read checked-in payloads instead of the live
endpoints — see `ingest/fixtures.py`. That's what CI does on pull requests.

Run:  uv run python -m ingest.pipeline
"""

from __future__ import annotations

import os
from collections.abc import Iterable

import dlt

from ingest import fixtures
from ingest.sources.ecb import ecb_fx_rates
from ingest.sources.eurostat import eu_elec_prices
from ingest.sources.owid import owid_co2, owid_energy
from ingest.sources.retail import retail_invoice_lines
from ingest.sources.weather import om_weather_daily
from ingest.sources.worldbank import wb_country, wb_wdi
from lake.lakehouse import dlt_credentials


@dlt.source
def public_indicators(
    wdi_years: tuple[int, int] | None = None,
    retail_months: tuple[str, str] | None = None,
    weather_years: tuple[int, int] | None = None,
):
    """The eight resources as one dlt source.

    The window arguments are passed to their resources, so a Dagster partition
    range and an unpartitioned CLI run build the source with the same call. One
    per resource: the grains differ (years, months), and WDI and weather are
    separate assets, so a backfill of one is not a backfill of the other.
    """
    return [
        owid_co2(),
        owid_energy(),
        wb_country(),
        wb_wdi(wdi_years),
        eu_elec_prices(),
        ecb_fx_rates(),
        retail_invoice_lines(retail_months),
        om_weather_daily(weather_years),
    ]


# Drop and re-infer the schema of the resources being loaded, so a type or column
# change at the source is not masked by dlt's persisted, widen-only schema.
# `drop_sources` would also drop the resources a partial (Dagster) run skipped.
REFRESH = "drop_resources"

# `refresh` applies to a whole run, and it would drop an incremental resource's
# table and watermark. So the dispositions load in two calls: replace with
# `refresh`, merge without it.
FULL_REFRESH_RESOURCES = ("owid_co2", "owid_energy", "wb_country", "eu_elec_prices")
INCREMENTAL_RESOURCES = ("wb_wdi", "ecb_fx_rates", "retail_invoice_lines", "om_weather_daily")

# Which resources the orchestration layer partitions — a different question from
# which merge. A partition must be a re-runnable unit of work that maps onto a
# slice of the destination:
#
#   * `wb_wdi` — the API takes `&date=lo:hi` and `year` is in the primary key.
#   * `retail_invoice_lines` — one static workbook, so the fetch cannot narrow,
#     but the load can: `invoice_month` comes from the partition's timestamp.
#   * `om_weather_daily` — the API takes a date range, `weather_date` is in the
#     primary key, and a year is the unit its budget is spent in: the full
#     archive costs more than a day's allowance.
#
# `ecb_fx_rates` merges but is not partitioned: its whole series is one request.
# Kept here, not in `orchestration/`, so `tests/test_ingest.py` can hold both
# splits to the source without importing Dagster.
PARTITIONED_RESOURCES = ("wb_wdi", "retail_invoice_lines", "om_weather_daily")


def load_groups(resources: Iterable[str] | None = None) -> list[tuple[list[str], dict]]:
    """The resource groups to load, in order, each with the `run()` kwargs it needs.

    `resources` restricts the result to a subset — Dagster passes whichever raw
    assets were selected. Empty groups are dropped, so materialising `raw/wb_wdi`
    alone doesn't try to run a load with no resources in it.
    """
    wanted = None if resources is None else set(resources)
    groups = []
    for names, kwargs in (
        (FULL_REFRESH_RESOURCES, {"refresh": REFRESH}),
        (INCREMENTAL_RESOURCES, {}),
    ):
        selected = [name for name in names if wanted is None or name in wanted]
        if selected:
            groups.append((selected, kwargs))
    return groups


# The dataset dlt loads into — the `raw` schema every landing table lands in.
# Named because `publish/restore_history.py` carries a table *into* it and has to
# agree with this about which schema that is.
PIPELINE_DATASET = "raw"


def pipeline_name() -> str:
    """`modern_data_stack`, or `modern_data_stack_fixtures` under fixtures.

    A function rather than a constant because `fixtures.enabled()` reads the
    environment, which a test may set after import. Split out of
    `build_pipeline` because the restore script needs the name to locate dlt's
    *local* state directory, and must do so without building a pipeline —
    constructing one is what would create the state it is checking for.
    """
    return f"modern_data_stack{'_fixtures' if fixtures.enabled() else ''}"


def build_pipeline() -> dlt.Pipeline:
    """The one dlt pipeline definition, shared by the CLI and the Dagster assets.

    A fixture run gets its own pipeline name because dlt keys state on the name,
    not the destination: a fixture run's WDI watermark would otherwise make the
    next real run fetch only a recent window into a warehouse without the history.

    The state directory is `~/.dlt/pipelines/<name>/` if `~/.dlt` exists, else
    `$XDG_DATA_HOME/dlt/pipelines/<name>/`; both can exist with only the first
    live, which is why `just dlt-state` asks dlt rather than listing a directory.
    """
    # dlt adds `_dlt_load_id` to row objects but not, by default, to Arrow data,
    # so `raw.retail_invoice_lines` would land without it — silently breaking
    # `dbt source freshness`, `pipeline_sources` and `source_loaded_at` for that
    # table. Set here so every caller of this function gets it.
    os.environ.setdefault("NORMALIZE__PARQUET_NORMALIZER__ADD_DLT_LOAD_ID", "true")

    # `raw` lands in the DuckLake catalog, the only copy of the landing tables;
    # dbt attaches it and builds into the DuckDB file. dlt's merge regenerates
    # `_dlt_id`/`_dlt_load_id` on every touched row, so the catalog's change feed
    # cannot see a real revision — `lake.lakehouse.revisions()` diffs snapshots.
    return dlt.pipeline(
        pipeline_name=pipeline_name(),
        destination=dlt.destinations.ducklake(dlt_credentials()),
        dataset_name=PIPELINE_DATASET,
    )


def main() -> None:
    pipeline = build_pipeline()
    for names, kwargs in load_groups():
        print(pipeline.run(public_indicators().with_resources(*names), **kwargs))


if __name__ == "__main__":
    main()
