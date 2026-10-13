"""Eurostat half-yearly electricity prices, served as JSON-stat.

The payload is a flat `value` dict keyed by a row-major index over every
dimension, so the resource filters all but `geo`/`time` server-side and walks
what is left.
"""

from __future__ import annotations

import dlt

from ingest import http

# Eurostat: electricity prices for household consumers, medium consumption band
# DC (2 500–4 999 kWh/yr), all taxes included, in EUR/kWh. Filtered server-side to
# geo × time so the JSON-stat payload stays small. One dict, because the resource
# checks the response against the same filters the request sent.
# https://ec.europa.eu/eurostat/databrowser/view/nrg_pc_204
EU_ELEC_PRICES_FILTERS = {
    "nrg_cons": "KWH2500-4999",
    "tax": "I_TAX",
    "currency": "EUR",
    "unit": "KWH",
}
EU_ELEC_PRICES_API = (
    "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/nrg_pc_204"
    "?format=JSON&lang=EN&" + "&".join(f"{k}={v}" for k, v in EU_ELEC_PRICES_FILTERS.items())
)

# The two dimensions the walk iterates; every other one must hold one category.
WALKED_DIMENSIONS = ("geo", "time")


@dlt.resource(name="eu_elec_prices", write_disposition="replace")
def eu_elec_prices():
    # Every dimension but geo and time has one category (see the URL), so each
    # flat index is geo * stride + time * stride.
    j = http.get_json_object(EU_ELEC_PRICES_API)
    dim_ids: list[str] = j["id"]
    sizes: list[int] = j["size"]
    values: dict[str, float] = j["value"]
    check_one_category_per_filter(j)

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


def check_one_category_per_filter(cube: dict) -> None:
    """Refuse a cube whose filtered dimensions are not the one category asked for.

    The walk computes `geo * stride + time * stride` and so reads index 0 of every
    other dimension. That is right only while each holds exactly the category the
    URL filtered to. If a filter stopped applying, a renamed code or a parameter
    the API ignored, the walk would return the first category under this one's
    label: the price excluding tax as the price including it, with no error and
    a plausible number. Measured on a two-category `tax` cube before this check:
    0.10 came back where 0.25 was the answer.
    """
    for name, size in zip(cube["id"], cube["size"]):
        if name in WALKED_DIMENSIONS:
            continue
        categories = list(cube["dimension"][name]["category"]["index"])
        wanted = EU_ELEC_PRICES_FILTERS.get(name)
        if size != 1 or (wanted is not None and categories != [wanted]):
            raise RuntimeError(
                f"Eurostat nrg_pc_204 returned {categories[:5]} for `{name}`"
                f"{f', not the one category requested ({wanted!r})' if wanted else ''}. "
                "The JSON-stat walk reads the first category of every dimension but "
                "geo and time, so it would label another series' prices as this one's."
            )
