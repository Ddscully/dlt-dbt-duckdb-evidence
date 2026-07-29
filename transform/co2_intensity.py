"""Polars heavy-transform layer: read a dbt mart from DuckDB, compute a
derived metric, and write it back as a table Evidence can query.

Demonstrates using Polars for window/ranking logic that's clumsy in SQL.

Run:  uv run python -m transform.co2_intensity
"""

from __future__ import annotations

import duckdb
import polars as pl

from ingest.pipeline import DUCKDB_PATH


def build_co2_intensity(df: pl.DataFrame) -> pl.DataFrame:
    """Rank carbon efficiency within each (income group, year) cohort.

    Intensity is derived here rather than taken from OWID's `co2_per_gdp`,
    which is carried through the mart but stops in 2022 and only ever covered
    164 countries. Recomputing from World Bank GDP tracks the mart's own
    coverage — ~197 countries through 2024.

    The denominator is **constant 2015 US$** (`NY.GDP.MKTP.KD`), not current
    US$. Current-dollar GDP moves with inflation and the exchange rate, so it
    measures currency as much as carbon: on that basis Japan cut emissions 21%
    between 2010 and 2024 and still scored 10% *worse*, because the yen fell
    28% against the dollar over the same span.

    Note the basis differs from OWID's column (kg CO2 per 2011 international-$,
    PPP), so levels aren't comparable between the two and ranking uses only the
    derived column.
    """
    kg_per_mt = 1e9  # co2_mt is million tonnes; 1 Mt = 1e9 kg
    return (
        df.with_columns(
            pl.when(pl.col("gdp_constant_usd") > 0)
            .then(pl.col("co2_mt") * kg_per_mt / pl.col("gdp_constant_usd"))
            .otherwise(None)
            .alias("co2_per_gdp_const_usd"),
        )
        .filter(pl.col("co2_per_gdp_const_usd").is_not_null())
        .with_columns(
            # rank carbon efficiency within each income group per year
            pl.col("co2_per_gdp_const_usd")
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
