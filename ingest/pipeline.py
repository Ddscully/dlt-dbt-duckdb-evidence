"""dlt ingestion: pull public CSV/JSON sources into the DuckDB warehouse.

Sources (all freely licensed, country + year keyed):
  - OWID CO2 & GHG        https://github.com/owid/co2-data
  - OWID Energy           https://github.com/owid/energy-data
  - World Bank WDI        https://databank.worldbank.org/source/world-development-indicators
  - World Bank countries  https://api.worldbank.org/v2/country?format=json  (dimension table)

Run:  uv run python -m ingest.pipeline
"""

from __future__ import annotations

import dlt
import polars as pl

DUCKDB_PATH = "data/warehouse.duckdb"

OWID_CO2 = "https://raw.githubusercontent.com/owid/co2-data/master/owid-co2-data.csv"
OWID_ENERGY = "https://raw.githubusercontent.com/owid/energy-data/master/owid-energy-data.csv"
WB_COUNTRY_API = "https://api.worldbank.org/v2/country?format=json&per_page=400"

# World Bank WDI indicators to pull (economic + social facts, country-year grain).
# https://databank.worldbank.org/source/world-development-indicators
WB_WDI_INDICATORS = {
    "NY.GDP.PCAP.CD": "gdp_per_capita_usd",  # GDP per capita, current US$
    "NY.GDP.MKTP.CD": "gdp_usd",  # GDP, current US$
    "SP.DYN.LE00.IN": "life_expectancy",  # Life expectancy at birth, years
    "SP.POP.TOTL": "population",  # Population, total
    "SI.POV.DDAY": "poverty_rate",  # Poverty headcount at $2.15/day, % of pop
}


# infer_schema_length=None scans the whole file so sparse numeric columns
# (empty for the first rows) are typed as numbers, not strings.
@dlt.resource(name="owid_co2", write_disposition="replace")
def owid_co2():
    yield pl.read_csv(OWID_CO2, infer_schema_length=None).to_dicts()


@dlt.resource(name="owid_energy", write_disposition="replace")
def owid_energy():
    yield pl.read_csv(OWID_ENERGY, infer_schema_length=None).to_dicts()


@dlt.resource(name="wb_country", write_disposition="replace")
def wb_country():
    import requests

    # World Bank returns [metadata, [records...]]
    payload = requests.get(WB_COUNTRY_API, timeout=60).json()
    yield payload[1]


@dlt.resource(name="wb_wdi", write_disposition="replace")
def wb_wdi():
    import requests

    # One request per indicator; per_page large enough to avoid pagination.
    for code in WB_WDI_INDICATORS:
        url = (
            f"https://api.worldbank.org/v2/country/all/indicator/{code}"
            "?format=json&per_page=20000"
        )
        payload = requests.get(url, timeout=120).json()
        for row in payload[1] or []:
            yield {
                "indicator": code,
                "country_iso3": row.get("countryiso3code"),
                "year": int(row["date"]) if row.get("date") else None,
                "value": row.get("value"),
            }


@dlt.source
def public_indicators():
    return [owid_co2(), owid_energy(), wb_country(), wb_wdi()]


def main() -> None:
    pipeline = dlt.pipeline(
        pipeline_name="modern_data_stack",
        destination=dlt.destinations.duckdb(DUCKDB_PATH),
        dataset_name="raw",
    )
    # drop_sources = re-infer the schema from source each run, so type/column
    # changes at the source aren't masked by dlt's persisted (widen-only) schema.
    info = pipeline.run(public_indicators(), refresh="drop_sources")
    print(info)


if __name__ == "__main__":
    main()
