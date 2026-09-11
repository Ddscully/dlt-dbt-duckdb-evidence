"""ECB euro reference rates, via Frankfurter.

Daily. Incremental on the last published fixing, and deliberately *not*
partitioned: the whole series since 1999 is one request.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import dlt
from dlt.common.schema.typing import TColumnSchema

from ingest import http

# Frankfurter republishes the ECB's daily euro foreign-exchange reference rates
# as JSON — no key, no quota, no auth. https://frankfurter.dev
FRANKFURTER_API = "https://api.frankfurter.dev/v1"

# The ECB quotes only against the euro; a USD-based rate is a division in
# `stg_fx_rates`, not a second request.
FX_BASE_CURRENCY = "EUR"

# The first reference-rate day. Earlier dates return an empty `rates` object.
FX_FIRST_DATE = "1999-01-04"

# How far back an incremental run re-asks. Short, because the ECB does not
# restate fixings the way the World Bank restates years: ten days closes a hole
# left by a failed run or a rare correction, and the merge key makes it free.
FX_LOOKBACK_DAYS = 10

# One watermark for the table, unlike WDI's per-indicator ones: every currency
# arrives in the same request, so a newly listed currency is already covered.
FX_WATERMARK_KEY = "max_rate_date"

FX_PRIMARY_KEY = ("rate_date", "quote_currency")

# Declared because a merge resource keeps dlt's widen-only schema, and `rate`
# must be a double however the first window happens to look.
FX_COLUMNS: dict[str, TColumnSchema] = {
    "rate_date": {"data_type": "date", "nullable": False},
    "base_currency": {"data_type": "text", "nullable": False},
    "quote_currency": {"data_type": "text", "nullable": False},
    "rate": {"data_type": "double"},
}


def fx_url(start_date: str, end_date: str | None = None) -> str:
    """The Frankfurter request URL for one date range (also used by the recorder).

    The whole series is one unpaginated request (1999 to today is 3.6 MB).
    """
    end = end_date or datetime.now(UTC).date().isoformat()
    return f"{FRANKFURTER_API}/{start_date}..{end}?base={FX_BASE_CURRENCY}"


def fx_start_date(last_loaded_date: str | None) -> str:
    """The first date the next FX load should ask for.

    `FX_FIRST_DATE` until a load has recorded a watermark, then
    `FX_LOOKBACK_DAYS` back from it — clamped, so a watermark near the start of
    the series can't ask for dates before the euro existed.
    """
    if last_loaded_date is None:
        return FX_FIRST_DATE
    start = date.fromisoformat(last_loaded_date) - timedelta(days=FX_LOOKBACK_DAYS - 1)
    return max(start, date.fromisoformat(FX_FIRST_DATE)).isoformat()


@dlt.resource(
    name="ecb_fx_rates",
    write_disposition="merge",
    primary_key=FX_PRIMARY_KEY,
    columns=FX_COLUMNS,
)
def ecb_fx_rates():
    """The ECB's daily euro reference rates, one row per (date, currency).

    The wide payload (`{"2024-01-02": {"USD": 1.0956, ...}, ...}`) is unpivoted
    here, so the landing table is at the merge key's grain and a newly listed
    currency is a row rather than a column.

    The currency panel changes over time — currencies stop at euro adoption or
    suspension, and ISK has a nine-year interior gap — which is what
    `marts.fct_fx_rates_daily`'s carry-forward has to handle (the
    `currency-and-calendar` skill has the shapes).

    Under fixtures the whole series comes back whatever window is asked for;
    the merge key makes that harmless.
    """
    state = dlt.current.resource_state()
    payload = http.get_json_object(fx_url(fx_start_date(state.get(FX_WATERMARK_KEY))))
    rates: dict[str, dict[str, float]] = payload.get("rates") or {}

    # ISO dates sort lexicographically, so `max` is the newest day. Only on a
    # non-empty response — a weekend-only window legitimately returns nothing —
    # and dlt commits the state only if the load succeeds.
    if rates:
        state[FX_WATERMARK_KEY] = max(rates)

    yield [
        {
            "rate_date": day,
            "base_currency": payload.get("base", FX_BASE_CURRENCY),
            "quote_currency": currency,
            "rate": value,
        }
        for day, quotes in rates.items()
        for currency, value in quotes.items()
    ]
