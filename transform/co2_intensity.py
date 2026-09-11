"""Polars transform: read a dbt mart from DuckDB, derive carbon intensity, and
write it back as a table Evidence can query.

Run:  uv run python -m transform.co2_intensity
"""

from __future__ import annotations

import duckdb
import polars as pl

from modern_data_stack import db
from modern_data_stack.paths import warehouse_path

DUCKDB_PATH = warehouse_path()


def build_co2_intensity(df: pl.DataFrame) -> pl.DataFrame:
    """Rank carbon efficiency within each (income group, year) cohort.

    Derived from World Bank GDP rather than taken from OWID's intensity column
    (`co2_kg_per_gdp_ppp_2011` in the mart), which stops in 2022 at 164
    countries; the derived one covers ~195 in 2024.

    The denominator is constant 2015 US$ (`NY.GDP.MKTP.KD`). Current dollars move
    with inflation and the exchange rate: from 2010 to 2024 Japan cut emissions
    21% while its current-dollar GDP fell 28% on a weaker yen, so it would score
    10% *worse*; its real GDP grew 10%.

    OWID's column is kg per 2011 international-$ (PPP), so levels are not
    comparable, and ranking uses only the derived column.
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
        db.write_frames(con, {"co2_intensity": out}, "analytics")
        return out.height
    finally:
        con.close()


def main() -> None:
    print(f"wrote analytics.co2_intensity ({run()} rows)")


if __name__ == "__main__":
    main()
