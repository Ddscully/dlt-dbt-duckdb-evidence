"""dlt ingestion: pull public CSV/JSON sources into the DuckDB warehouse.

Sources (all freely licensed; country + year keyed apart from the last):
  - OWID CO2 & GHG        https://github.com/owid/co2-data
  - OWID Energy           https://github.com/owid/energy-data
  - World Bank WDI        https://databank.worldbank.org/source/world-development-indicators
  - World Bank countries  https://api.worldbank.org/v2/country?format=json  (dimension table)
  - Eurostat prices       https://ec.europa.eu/eurostat/databrowser/view/nrg_pc_204  (EU only)
  - ECB reference rates   https://frankfurter.dev  (daily FX — the one sub-annual grain)
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

# Where this layer writes is now `LAKEHOUSE_DIR`, not `WAREHOUSE_PATH` — see
# `lake.lakehouse`. The env var that a fixture run overrides changed with it, and
# `just test-pipeline` sets both, because dbt still needs a throwaway warehouse
# to build into even though nothing here writes to one.


@dlt.source
def public_indicators(
    wdi_years: tuple[int, int] | None = None,
    retail_months: tuple[str, str] | None = None,
    weather_years: tuple[int, int] | None = None,
):
    """The eight resources as one dlt source.

    The three window arguments are threaded through to their resources rather
    than bound on afterwards, so the Dagster asset can build a source for one
    partition range with the same call the CLI makes for an unpartitioned run.
    They are separate arguments because the three are partitioned on different
    columns at different grains — years for WDI and weather, months for retail —
    and a shared one would have to be a date range that none of them takes
    directly. WDI and weather are both yearly and still keep their own argument:
    they are separate assets, and a backfill of one is not a backfill of the
    other.
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


# Drop + re-infer the schema of the resources actually being loaded, so type or
# column changes at the source aren't masked by dlt's persisted (widen-only)
# schema. Unlike `drop_sources` this is safe when only part of the source runs,
# which is what Dagster does when you materialise a single raw asset.
REFRESH = "drop_resources"

# `refresh` is a property of a *run*, not of a resource, and dropping `wb_wdi`
# would take its table and its watermark with it — a full reload wearing an
# incremental costume. So the two dispositions load in two calls: replace with
# the schema-safety refresh, merge without it.
FULL_REFRESH_RESOURCES = ("owid_co2", "owid_energy", "wb_country", "eu_elec_prices")
INCREMENTAL_RESOURCES = ("wb_wdi", "ecb_fx_rates", "retail_invoice_lines", "om_weather_daily")

# Which of those the orchestration layer partitions, and it is not the same
# question as which of them merge. Three answers, and the third is the one that
# says what the rule actually is:
#
#   * `wb_wdi` — the *fetch* narrows. The API takes `&date=lo:hi` and `year` is in
#     the primary key, so a year is a re-runnable unit of work end to end.
#   * `ecb_fx_rates` — merges for the same reason WDI does and is deliberately
#     *not* partitioned. Its entire 27-year series is one three-second request, so
#     a daily partition would be 7,000 Dagster partitions standing in for it.
#   * `retail_invoice_lines` — the fetch cannot narrow at all: the source is one
#     static 45 MB workbook and there is no request to make a window out of. What
#     narrows is the *load*. Reading and converting a month is real work, the
#     cached download means twenty-five partitions are still one fetch, and
#     `invoice_month` is derived from the same timestamp the partition is keyed
#     on, so re-running one month replaces exactly that month.
#   * `om_weather_daily` — the strongest case of the four, and the first where a
#     partition is about *money* rather than time. The API takes a date range,
#     `weather_date` is in the primary key, and unlike WDI the full history
#     genuinely cannot be fetched in one run: 41 capitals over 2007-2025 is
#     ~12,200 units against a 10,000-a-day allowance. A year is the unit the
#     budget is spent in, so it had better be the unit the graph can point at.
#
# So the rule is not "the API takes a range" and never was "the disposition is
# merge" — it is whether a partition is a re-runnable unit of *work* that maps
# cleanly onto a slice of the destination. Kept here rather than in
# `orchestration/` so the covering tests in `tests/test_ingest.py` can hold both
# splits to the source without importing Dagster, an optional dependency group.
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

    A fixture run gets its own pipeline name, which is what keeps its *state*
    out of the real pipeline's. dlt keeps state in its own pipelines directory,
    keyed on the pipeline name alone — not on the destination — so a fixture run
    leaving a WDI watermark behind would hand it to the next real run, which
    would then fetch a five-year window on the assumption that history it never
    loaded is already there.

    That directory is `~/.dlt/pipelines/<name>/` **if `~/.dlt` already exists**,
    and `$XDG_DATA_HOME/dlt/pipelines/<name>/` otherwise — dlt prefers the legacy
    location when it finds one and says so in a UserWarning. Both paths can exist
    on one machine with only the first live, so read the warning rather than the
    directory listing: `just dlt-state` asks dlt instead of guessing.
    """
    # **Arrow data does not get `_dlt_load_id` unless you ask for it.** dlt adds
    # that column when it normalises row objects, but the Arrow/Parquet path
    # writes the table through untouched and the load-id normaliser is off by
    # default — so `raw.retail_invoice_lines` landed without the one column every
    # other table here has, and lost it silently: nothing errors, the load
    # succeeds, and the table simply has no provenance.
    #
    # It costs three things, none of them loud. `dbt source freshness` reads
    # exactly this column, so the source can never be checked. `pipeline_sources`
    # skips any table lacking it, so the observability page under-reports by one
    # row and looks complete. And `source_loaded_at` — the "which extract did
    # this number come from" column that `dim_grid_emission_factors` exists to
    # carry — has nothing to read.
    #
    # Set here rather than in an env var or `.dlt/config.toml` so it travels with
    # the pipeline definition: the Dagster asset, the CLI and the tests all build
    # their pipeline through this function, and a config file would be a fourth
    # place to remember.
    os.environ.setdefault("NORMALIZE__PARQUET_NORMALIZER__ADD_DLT_LOAD_ID", "true")

    # **The destination is the lakehouse, not the DuckDB file.** `raw` lands as
    # Parquet under a DuckLake catalog; dbt attaches that catalog and builds
    # `staging`/`marts` into `data/warehouse.duckdb`, which is then the whole of
    # what gets published. Two consequences worth knowing here rather than three
    # modules away:
    #
    # * dlt's merge regenerates `_dlt_id` and `_dlt_load_id` on every row it
    #   touches, so DuckLake's change feed reports a no-op reload and a one-row
    #   restatement identically. `lake.lakehouse.revisions()` diffs two
    #   snapshots instead, projecting those columns away.
    # * the catalog is now the only copy of the landing tables, which makes it
    #   the state a rebuild cannot reproduce — the role `data/warehouse.duckdb`
    #   used to hold for `raw.om_weather_daily`.
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
