"""Shared HTTP fetching for the ingest layer.

Three functions, all fixture-aware: `INGEST_FIXTURES=1` routes them through
`ingest.fixtures` instead of the network, which is what makes CI offline. They
share one retry loop, which logs every failed attempt and wait.

**Call these as `http.get_json(...)`, never `from ingest.http import get_json`.**
`tests/test_ingest.py` patches `setattr(http, "get_json", ...)`; a name bound
into a source module would not be looked up through that patch, and the test
would pass while exercising the real fetch path.
"""

from __future__ import annotations

import gzip
import json
import logging
import time
from collections.abc import Callable

import requests

from ingest import fixtures

# Logged, not printed: Dagster captures a step's stdout (`ingest.sources.weather`).
log = logging.getLogger(__name__)

# Waits of 4, 8, 16 and 32 s between five attempts: a minute, which outlasts the
# transient error pages these publishers serve. A healthy fetch never sleeps.
RETRIES = 5
BACKOFF_SECONDS = 4.0


def retryable(exc: Exception) -> bool:
    """Whether a later identical request could succeed.

    A 5xx, 429, timeout, reset or unparseable body could. Any other 4xx could
    not, as in `ingest.sources.weather.get_weather_json`: the request itself is
    wrong, and a minute of retries would get the same answer.
    """
    if isinstance(exc, requests.HTTPError):
        status = exc.response.status_code if exc.response is not None else 0
        return not (400 <= status < 500 and status != 429)
    return isinstance(exc, (requests.RequestException, ValueError))  # ValueError = bad JSON


def backoff(url: str, attempt: int, retries: int, exc: Exception) -> None:
    """Log a failed attempt and sleep before the next one; nothing after the last."""
    if attempt >= retries - 1:
        return
    wait = BACKOFF_SECONDS * 2**attempt
    log.info("%s: attempt %d of %d failed (%s); retrying in %.0f s",
             url, attempt + 1, retries, exc, wait)  # fmt: skip
    time.sleep(wait)


def _fetch[T](
    url: str, *, what: str, timeout: int, retries: int, read: Callable[[requests.Response], T]
) -> T:
    """GET with retries, `read` turning the response into the result, and
    `what` naming the result in the error raised when every attempt failed.

    `raise_for_status()` comes first: without it an HTML or JSON error body
    would read fine and be handed on as if it were data. A `ValueError` from
    `read` (an unparseable body) is retried like a 5xx.
    """
    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()
            return read(resp)
        except (requests.RequestException, ValueError) as exc:
            if not retryable(exc):
                raise
            last = exc
            backoff(url, attempt, retries, exc)
    raise RuntimeError(f"failed to fetch {what} from {url}: {last}")


def get_json(url: str, *, timeout: int = 120, retries: int = RETRIES) -> dict | list:
    """GET + parse JSON with retries — the World Bank & Eurostat APIs
    occasionally return a transient error page or non-JSON body.

    Retried with doubling waits and ultimately raised; see `retryable` for what
    is retried and `_fetch` for why an error status is never parsed as data.
    """
    if fixtures.enabled():
        path = fixtures.path_for(url)
        # The FX fixture is gzipped: it is kept whole, because the models are
        # tested against its discontinuities, and whole it is 3.6 MB of JSON.
        if path.suffix == ".gz":
            return json.loads(gzip.decompress(path.read_bytes()))
        return json.loads(path.read_text())
    return _fetch(url, what="JSON", timeout=timeout, retries=retries, read=lambda resp: resp.json())


def get_bytes(url: str, *, timeout: int = 120, retries: int = RETRIES) -> bytes:
    """GET a whole file's body with the same retries — OWID's CSVs."""
    if fixtures.enabled():
        # Gzipped like the FX fixture; what comes back is what the URL serves.
        path = fixtures.path_for(url)
        body = path.read_bytes()
        return gzip.decompress(body) if path.suffix == ".gz" else body
    return _fetch(
        url, what="the file", timeout=timeout, retries=retries, read=lambda resp: resp.content
    )


def get_json_object(url: str, *, timeout: int = 120, retries: int = RETRIES) -> dict:
    """`get_json` for an endpoint that documents a JSON *object*.

    `get_json` returns `dict | list` because the World Bank sends
    `[metadata, [records…]]`; its callers narrow that by hand. Eurostat's
    JSON-stat and the ECB's `{"rates": …}` are objects, and this narrows once for
    them. It checks the shape only: an error *object* served with a 200 passes
    it, so each caller checks the keys it reads (`ecb_fx_rates` does).
    """
    payload = get_json(url, timeout=timeout, retries=retries)
    if not isinstance(payload, dict):
        # TRY004 asks for TypeError, but no argument was wrong — the server sent
        # the wrong shape, which the World Bank checks in `ingest.sources.worldbank`
        # also raise RuntimeError for.
        raise RuntimeError(  # noqa: TRY004
            f"expected a JSON object from {url}, got {payload!r:.300}"
        )
    return payload
