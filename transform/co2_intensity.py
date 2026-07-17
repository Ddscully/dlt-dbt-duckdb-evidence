"""Polars heavy-transform layer: read a dbt mart from DuckDB, compute a
derived metric, and write it back as a table Evidence can query.

Demonstrates using Polars for window/ranking logic that's clumsy in SQL.

Run:  uv run python -m transform.co2_intensity
"""

from __future__ import annotations

import duckdb
import polars as pl

DUCKDB_PATH = "data/warehouse.duckdb"


def main() -> None:
    con = duckdb.connect(DUCKDB_PATH)
    df = con.sql("select * from marts.fct_emissions_energy").pl()

    out = (
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

    con.sql("create schema if not exists analytics")
    con.register("out_df", out.to_pandas())
    con.sql("create or replace table analytics.co2_intensity as select * from out_df")
    print(f"wrote analytics.co2_intensity ({out.height} rows)")


if __name__ == "__main__":
    main()
