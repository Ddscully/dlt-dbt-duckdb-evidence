"""modern-data-stack: the domain-neutral half of this repo's pipeline.

The project layers (`ingest/`, `dbt/`, `transform/`, `lake/`, `reports/`,
`orchestration/`) are the worked example — emissions, energy and development
data for ~200 countries. This package is what's left when you take the dataset
out of them:

* `paths`         — where the project root, the warehouse and the lake are
* `fixtures`      — serving recorded payloads instead of live endpoints
* `lake`          — hive-partitioned Parquet archive of warehouse tables
* `observability` — dlt/dbt/DuckDB metadata as queryable tables
* `export`        — packaging a warehouse as a publishable artifact
* `history`       — carrying a dbt snapshot forward between builds
* `db`            — single-row and scalar reads, without the Optional

Each takes its configuration as arguments; the project modules that call them
hold the constants and stay the entry points, so `python -m lake.archive` and
friends keep working. See `docs/REUSING_THIS_STACK.md`.

Importing this package pulls in none of the submodules, so it costs no Polars or
DuckDB import to a caller that only wants `paths`.

Deliberately *not* here: `RawSchemaDltTranslator`, which stays in
`orchestration/assets.py`. Keying dlt resources as `raw/<resource>` so they meet
the keys dagster-dbt derives is the convention worth stealing from this repo, but
the code for it is twenty lines wrapped around two of that module's constants,
and moving it would put Dagster (an optional dependency group) behind a package
import.

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
