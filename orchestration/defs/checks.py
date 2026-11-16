"""Asset checks — the ones dbt can't express.

One module rather than one beside each asset, because `tests/test_asset_checks.py`
points the checks at throwaway files by patching this module's globals:
`DUCKDB_PATH`, `LAKEHOUSE_DIR`, `WB_WDI_INDICATORS`, `BUILD_DIR`, `page_routes`
and `dbt_run_results_path`. Each must be a name *in this module* that the checks
read at call time. One imported into another module by name would keep the real
value there, and its check would read the real warehouse with the test green.
"""

# No `from __future__ import annotations`, to match the asset modules beside it.

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import dagster as dg
import duckdb

from ingest.sources.worldbank import WB_WDI_INDICATORS
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
from modern_data_stack.paths import dbt_run_results_path, warehouse_path
from orchestration.defs import analytics
from orchestration.defs.models import FCT_EMISSIONS_ENERGY, FCT_FX_RATES_DAILY
from orchestration.defs.site import EVIDENCE_SITE
from publish.build_report import BUILD_DIR, DBT_DOCS_ROUTE, page_routes

# The warehouse the asset checks read; their tests point it at a throwaway file.
DUCKDB_PATH = warehouse_path()

WEATHER_RAW = dg.AssetKey(["raw", "om_weather_daily"])

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
    The fetch re-requests the second at another page size and raises on a second
    stale copy, so this is the backstop for it. Reads the lakehouse: on a fresh checkout the warehouse does not exist yet.
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


@dg.asset_check(asset=analytics.co2_intensity, blocking=True)
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


@dg.asset_check(asset=analytics.retail_rfm, blocking=True)
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


@dg.asset_check(asset=analytics.pipeline_status, blocking=True)
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
            metadata={"versions": len(versions), "note": "fewer than two weather loads held"},
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
    """Every page in `reports/pages/` has HTML in `reports/build/`, and so do the dbt docs.

    `evidence build` exits 0 for a site missing a page, and a route that
    rendered only the shell looks most like success, so size is checked too.
    """
    routes = {**page_routes(), DBT_DOCS_ROUTE: BUILD_DIR / DBT_DOCS_ROUTE / "index.html"}
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
