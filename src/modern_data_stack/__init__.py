"""modern-data-stack: a public demo of a lightweight data-engineering + BI stack.

The `mds` console script is a thin convenience wrapper; the canonical entry
points are the `just` recipes and the module runners under ingest/ and transform/.
"""


def main() -> None:
    print(
        "modern-data-stack\n"
        "  just run        # ingest -> dbt build -> polars transform\n"
        "  just sql        # explore the DuckDB warehouse in Harlequin\n"
        "  just notebook   # marimo exploration\n"
        "See README.md for the full pipeline."
    )
