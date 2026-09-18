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


def build_co2_intensity(frame: pl.LazyFrame) -> pl.LazyFrame:
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

    Lazy in and out, so the caller decides when to collect. `frame` holds only
    rows with a usable ratio: `read_usable_rows` filters them in SQL.
    """
    kg_per_mt = 1e9  # co2_mt is million tonnes; 1 Mt = 1e9 kg
    return (
        frame.with_columns(
            (pl.col("co2_mt") * kg_per_mt / pl.col("gdp_constant_usd")).alias(
                "co2_per_gdp_const_usd"
            ),
        )
        .with_columns(
            # rank carbon efficiency within each income group per year
            pl.col("co2_per_gdp_const_usd")
            .rank(method="dense")
            .over(["income_group", "year"])
            .alias("co2_intensity_rank"),
        )
        # `country_iso3` last makes the order total: dense ranks tie.
        .sort(["year", "income_group", "co2_intensity_rank", "country_iso3"])
    )


def read_usable_rows(con: duckdb.DuckDBPyConnection) -> pl.LazyFrame:
    """The mart's rows that have a ratio, as a lazy frame.

    The filter is SQL, not a Polars `filter()`. Polars hands a lazy frame's
    predicate to DuckDB's bridge to translate, and that translation matched no
    rows in the live Pages build while matching 10,874 everywhere else; SQL
    leaves nothing to translate. Null, zero and negative GDP all drop out here
    rather than producing an infinity that would win the rank.
    """
    return con.sql(
        "select * from marts.fct_emissions_energy where gdp_constant_usd > 0 and co2_mt is not null"
    ).pl(lazy=True)


def run(duckdb_path: str = DUCKDB_PATH) -> int:
    """Read the mart, derive the metric, write `analytics.co2_intensity`.

    Returns the row count written (used as asset metadata by the orchestrator).
    """
    con = duckdb.connect(duckdb_path)
    try:
        out = build_co2_intensity(read_usable_rows(con)).collect()
        db.write_frames(con, {"co2_intensity": out}, "analytics")
        return out.height
    finally:
        con.close()


def main() -> None:
    print(f"wrote analytics.co2_intensity ({run()} rows)")


if __name__ == "__main__":
    main()
