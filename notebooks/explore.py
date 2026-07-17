"""Marimo reactive notebook for exploring the warehouse with Polars.

Run:  uv run --group notebook marimo edit notebooks/explore.py
"""

import marimo

app = marimo.App(width="medium")


@app.cell
def _():
    import duckdb
    import polars as pl

    con = duckdb.connect("data/warehouse.duckdb", read_only=True)
    return con, pl


@app.cell
def _(con):
    # Renewables adoption vs. emissions intensity, latest year
    df = con.sql(
        """
        select *
        from marts.fct_emissions_energy
        where year = (select max(year) from marts.fct_emissions_energy)
        """
    ).pl()
    df
    return (df,)


if __name__ == "__main__":
    app.run()
