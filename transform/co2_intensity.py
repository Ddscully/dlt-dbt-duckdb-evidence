"""Polars heavy-transform layer: read a dbt mart from DuckDB, compute a
derived metric, and write it back as a table Evidence can query.

Demonstrates using Polars for window/ranking logic that's clumsy in SQL.

Run:  uv run python -m transform.co2_intensity
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl

# Anchored to the repo root, not the cwd — see ingest/pipeline.py.
REPO_ROOT = Path(__file__).resolve().parent.parent
DUCKDB_PATH = str(REPO_ROOT / "data" / "warehouse.duckdb")


def build_co2_intensity(df: pl.DataFrame) -> pl.DataFrame:
    """Rank carbon efficiency within each (income group, year) cohort."""
    return (
        df.filter(pl.col("co2_per_gdp").is_not_null())
        .with_columns(
            # rank carbon efficiency within each income group per year
            pl.col("co2_per_gdp")
            .rank(method="dense")
            .over(["income_group", "year"])
            .alias("co2_intensity_rank"),
        )
        .sort(["year", "income_group", "co2_intensity_rank"])
    )


def run(duckdb_path: str = DUCKDB_PATH) -> int:
    """Read the mart, derive the metric, write `analytics.co2_intensity`.

    Returns the row count written (used as asset metadata by the orchestrator).
    """
    con = duckdb.connect(duckdb_path)
    try:
        out = build_co2_intensity(con.sql("select * from marts.fct_emissions_energy").pl())
        con.sql("create schema if not exists analytics")
        con.register("out_df", out)  # DuckDB reads Polars frames directly
        con.sql("create or replace table analytics.co2_intensity as select * from out_df")
        return out.height
    finally:
        con.close()


def main() -> None:
    print(f"wrote analytics.co2_intensity ({run()} rows)")


if __name__ == "__main__":
    main()
