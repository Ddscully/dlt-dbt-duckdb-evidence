"""ECB euro reference rates, via Frankfurter.

The one sub-annual source. Incremental on the last published fixing, and
deliberately *not* partitioned: the whole 27-year series is one request.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import dlt
from dlt.common.schema.typing import TColumnSchema

from ingest import http

# Frankfurter republishes the ECB's daily euro foreign-exchange reference rates
# as JSON — no key, no quota, no auth. https://frankfurter.dev
FRANKFURTER_API = "https://api.frankfurter.dev/v1"

# Every rate is quoted *against the euro*, because that is the only thing the ECB
# publishes. It is the source's shape, not a choice: a USD-based rate is a
# division in `stg_fx_rates`, not a second request.
FX_BASE_CURRENCY = "EUR"

# The first day the reference rates exist — the euro's third trading day. Asking
# for anything earlier just returns an empty `rates` object.
FX_FIRST_DATE = "1999-01-04"

# How far back an incremental run re-asks for. Deliberately much shorter than
# WDI's five *years*, because the reason is different: the World Bank restates
# published figures as routine practice, and the ECB does not restate a fixing.
# Ten days is what closes a hole left by a run that failed after its watermark
# moved, or by a rare corrected fixing — and the merge key makes re-asking free.
FX_LOOKBACK_DAYS = 10

# One watermark for the table, not one per currency — the opposite of WDI, and
# for a reason worth stating: every currency arrives in the *same* request, so a
# newly listed one is already covered by the table-wide high-water mark. WDI
# needs the per-indicator form precisely because adding an indicator adds a
# request that has never been made before.
FX_WATERMARK_KEY = "max_rate_date"

FX_PRIMARY_KEY = ("rate_date", "quote_currency")

# Declared for the same reason as `WDI_COLUMNS`: an incremental resource keeps
# dlt's persisted schema, which only widens. `rate` is the one that matters — the
# series spans 0.85765 (GBP) to 1,725,000 (the pre-2005 Turkish lira), and a
# lookback window that happened to hold only the majors would still infer double,
# but a first load restricted to one currency would not necessarily.
FX_COLUMNS: dict[str, TColumnSchema] = {
    "rate_date": {"data_type": "date", "nullable": False},
    "base_currency": {"data_type": "text", "nullable": False},
    "quote_currency": {"data_type": "text", "nullable": False},
    "rate": {"data_type": "double"},
}


def fx_url(start_date: str, end_date: str | None = None) -> str:
    """The Frankfurter request URL for one date range (also used by the recorder).

    The whole series is a single request — 1999 to today is 3.6 MB and answers in
    about three seconds — so there is no pagination to express here, unlike every
    other JSON source in this file.
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
    """The ECB's daily euro reference rates — the warehouse's first sub-annual grain.

    The payload is wide (`{"2024-01-02": {"USD": 1.0956, ...}, ...}`) and is
    unpivoted here rather than in staging, so `raw.ecb_fx_rates` lands at the
    grain the merge key is defined on. A wide landing table would need a new
    column every time the ECB lists a currency, which is exactly what dlt's
    widen-only schema handles worst.

    **The currency panel is not fixed, and that is the interesting part.** Of the
    46 codes in the series only 30 are still published. Ten stop on the last
    business day before their country adopted the euro (GRD in 2000, HRK in 2022,
    BGN in 2025); RUB stops on 2022-03-01; four more stop together in October
    2020; and ISK has a 3,341-day *interior* gap from Iceland's 2008 banking
    collapse to February 2018. Anything downstream that carries a rate forward
    has to reckon with all four shapes — see `marts.fct_fx_rates_daily`.

    Under fixtures the recorded payload is the whole series regardless of which
    window this asks for. That is harmless: the merge key means re-landing a date
    replaces it rather than duplicating it.
    """
    state = dlt.current.resource_state()
    payload = http.get_json_object(fx_url(fx_start_date(state.get(FX_WATERMARK_KEY))))
    rates: dict[str, dict[str, float]] = payload.get("rates") or {}

    # ISO dates sort lexicographically, so `max` over the keys is the newest day.
    # Moved only on a non-empty response, and before the yield so it doesn't
    # depend on dlt exhausting the generator: a request whose window falls
    # entirely on a weekend legitimately returns nothing, and advancing the
    # watermark there would advance it to a date that was never loaded. dlt only
    # commits resource state if the load itself succeeds.
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
