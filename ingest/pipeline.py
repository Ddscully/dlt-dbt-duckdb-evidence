"""dlt ingestion: pull public CSV/JSON sources into the DuckDB warehouse.

Sources (all freely licensed, country + year keyed):
  - OWID CO2 & GHG        https://github.com/owid/co2-data
  - OWID Energy           https://github.com/owid/energy-data
  - World Bank WDI        https://databank.worldbank.org/source/world-development-indicators
  - World Bank countries  https://api.worldbank.org/v2/country?format=json  (dimension table)
  - Eurostat prices       https://ec.europa.eu/eurostat/databrowser/view/nrg_pc_204  (EU only)

Run:  uv run python -m ingest.pipeline
"""

from __future__ import annotations

import time
from pathlib import Path

import dlt
import polars as pl
import requests

# Anchored to the repo root, not the cwd — the Dagster daemon and the CLI don't
# necessarily run from the project directory.
REPO_ROOT = Path(__file__).resolve().parent.parent
DUCKDB_PATH = str(REPO_ROOT / "data" / "warehouse.duckdb")

OWID_CO2 = "https://raw.githubusercontent.com/owid/co2-data/master/owid-co2-data.csv"
OWID_ENERGY = "https://raw.githubusercontent.com/owid/energy-data/master/owid-energy-data.csv"
WB_COUNTRY_API = "https://api.worldbank.org/v2/country?format=json&per_page=400"

# Eurostat: electricity prices for household consumers, medium consumption band
# DC (2 500–4 999 kWh/yr), all taxes included, in EUR/kWh. Filtered server-side to
# geo × time so the JSON-stat payload stays small.
# https://ec.europa.eu/eurostat/databrowser/view/nrg_pc_204
EU_ELEC_PRICES_API = (
    "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/nrg_pc_204"
    "?format=JSON&lang=EN&nrg_cons=KWH2500-4999&tax=I_TAX&currency=EUR&unit=KWH"
)

# World Bank WDI indicators to pull (economic + social facts, country-year grain).
# https://databank.worldbank.org/source/world-development-indicators
WB_WDI_INDICATORS = {
    "NY.GDP.PCAP.CD": "gdp_per_capita_usd",  # GDP per capita, current US$
    "NY.GDP.MKTP.CD": "gdp_usd",  # GDP, current US$
    "SP.DYN.LE00.IN": "life_expectancy",  # Life expectancy at birth, years
    "SP.POP.TOTL": "population",  # Population, total
    "SI.POV.DDAY": "poverty_rate",  # Poverty headcount at $2.15/day, % of pop
    "IT.NET.USER.ZS": "internet_users_pct",  # Individuals using the internet, % pop
    "SP.URB.TOTL.IN.ZS": "urban_pop_pct",  # Urban population, % of total
    "AG.LND.FRST.ZS": "forest_area_pct",  # Forest area, % of land area
    "EG.ELC.RNEW.ZS": "renew_elec_pct",  # Renewable electricity output, % of total
    "EG.IMP.CONS.ZS": "energy_imports_pct",  # Energy imports (net), % of energy use
}

# Page size for the World Bank API (its documented maximum is 32 000, but large
# pages occasionally time out — 10 000 with pagination is the safer trade).
WB_PER_PAGE = 10_000


def _get_json(url: str, *, timeout: int = 120, retries: int = 3) -> dict | list:
    """GET + parse JSON with a few retries — the World Bank & Eurostat APIs
    occasionally return a transient error page or non-JSON body.

    A non-2xx status is retried and ultimately raised: without the
    `raise_for_status()` an HTML/JSON error body would parse fine and be handed
    on as if it were data.
    """
    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:  # ValueError = JSONDecodeError
            last = exc
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"failed to fetch JSON from {url}: {last}")


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
    # World Bank returns [metadata, [records...]]
    payload = _get_json(WB_COUNTRY_API, timeout=60)
    yield payload[1]


@dlt.resource(name="wb_wdi", write_disposition="replace")
def wb_wdi():
    # Paginated: a single 20k-row page used to cover every indicator, but the
    # series grow by ~270 rows a year and were already at 87% of that cap, so
    # the next few years would have silently truncated the oldest indicators.
    for code in WB_WDI_INDICATORS:
        page = 1
        while True:
            url = (
                f"https://api.worldbank.org/v2/country/all/indicator/{code}"
                f"?format=json&per_page={WB_PER_PAGE}&page={page}"
            )
            payload = _get_json(url)
            # World Bank returns [metadata, [records...]]; anything else is an
            # error object served with a 200.
            if not (isinstance(payload, list) and len(payload) == 2):
                raise RuntimeError(f"unexpected World Bank payload for {code}: {payload!r:.300}")
            meta, rows = payload
            for row in rows or []:
                yield {
                    "indicator": code,
                    "country_iso3": row.get("countryiso3code"),
                    "year": int(row["date"]) if row.get("date") else None,
                    "value": row.get("value"),
                }
            if page >= int(meta.get("pages", 1)):
                break
            page += 1


@dlt.resource(name="eu_elec_prices", write_disposition="replace")
def eu_elec_prices():
    # Eurostat returns JSON-stat: a flat `value` dict keyed by the row-major
    # index over all dimensions. We filtered every dimension but geo & time to a
    # single category, so we walk geo × time and compute each flat index.
    j = _get_json(EU_ELEC_PRICES_API)
    dim_ids: list[str] = j["id"]
    sizes: list[int] = j["size"]
    values: dict[str, float] = j["value"]

    # Row-major strides: stride of the last dimension is 1, working leftwards.
    strides: dict[str, int] = {}
    acc = 1
    for name, size in zip(reversed(dim_ids), reversed(sizes)):
        strides[name] = acc
        acc *= size

    geo_index = j["dimension"]["geo"]["category"]["index"]
    time_index = j["dimension"]["time"]["category"]["index"]  # e.g. "2023-S1"
    for geo, gi in geo_index.items():
        for period, ti in time_index.items():
            flat = gi * strides["geo"] + ti * strides["time"]
            value = values.get(str(flat))
            if value is None:
                continue
            yield {
                "geo": geo,  # Eurostat 2-letter code (EL=Greece, UK=UK)
                "period": period,  # semi-annual, e.g. "2023-S1"
                "year": int(period[:4]),
                "price_eur_kwh": value,  # household price incl. all taxes
            }


@dlt.source
def public_indicators():
    return [owid_co2(), owid_energy(), wb_country(), wb_wdi(), eu_elec_prices()]


# Drop + re-infer the schema of the resources actually being loaded, so type or
# column changes at the source aren't masked by dlt's persisted (widen-only)
# schema. Unlike `drop_sources` this is safe when only part of the source runs,
# which is what Dagster does when you materialise a single raw asset.
REFRESH = "drop_resources"


def build_pipeline() -> dlt.Pipeline:
    """The one dlt pipeline definition, shared by the CLI and the Dagster assets."""
    return dlt.pipeline(
        pipeline_name="modern_data_stack",
        destination=dlt.destinations.duckdb(DUCKDB_PATH),
        dataset_name="raw",
    )


def main() -> None:
    info = build_pipeline().run(public_indicators(), refresh=REFRESH)
    print(info)


if __name__ == "__main__":
    main()
