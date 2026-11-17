"""Shared HTTP fetching for the ingest layer.

Four fetchers, all fixture-aware: `INGEST_FIXTURES=1` routes them through
`ingest.fixtures` instead of the network, which is what makes CI offline. They
share one retry loop, `_fetch`, which logs every failed attempt and wait.
(`ingest.sources.weather` keeps its own, paced by the Open-Meteo budget.)

**Call these as `http.get_json(...)`, never `from ingest.http import get_json`.**
`tests/test_ingest.py` patches `setattr(http, "get_json", ...)`; a name bound
into a source module would not be looked up through that patch, and the test
would pass while exercising the real fetch path.
"""

from __future__ import annotations

import gzip
import json
import logging
import shutil
import time
from collections.abc import Callable
from pathlib import Path

import requests

from ingest import fixtures
from modern_data_stack import files

# Logged, not printed: Dagster captures a step's stdout (`ingest.sources.weather`).
log = logging.getLogger(__name__)

# Waits of 4, 8, 16 and 32 s between five attempts: a minute, which outlasts the
# transient error pages these publishers serve. A healthy fetch never sleeps.
RETRIES = 5
BACKOFF_SECONDS = 4.0


# Errors in the request itself, which a later identical request repeats: the
# same rule as a 4xx. Most are also `ValueError`s, so they are named before the
# `ValueError` that means an unparseable body.
#
# `InvalidJSONError` is deliberately absent. It means a request body that would
# not serialise, which nothing here sends, but `resp.json()` raises its subclass
# `requests.exceptions.JSONDecodeError` for an error page served with a 200: the
# transient case `get_json` exists to retry.
PERMANENT_ERRORS = (
    requests.exceptions.InvalidHeader,
    requests.exceptions.InvalidURL,
    requests.exceptions.InvalidSchema,
    requests.exceptions.MissingSchema,
    requests.exceptions.URLRequired,
    requests.exceptions.TooManyRedirects,
)


# Request Timeout and Too Many Requests: the server ran out of time or patience,
# and the same request later can pass.
TRANSIENT_CLIENT_ERRORS = (408, 429)


def retryable(exc: Exception) -> bool:
    """Whether a later identical request could succeed.

    A 5xx, 408, 429, timeout, reset or unparseable body could: 408 and 429 are
    the 4xx that report the server's state, not the request's. Any other 4xx could
    not, as in `ingest.sources.weather.get_weather_json`: the request itself is
    wrong, and a minute of retries would get the same answer. Nor could a
    malformed URL or a redirect loop (`PERMANENT_ERRORS`).
    """
    if isinstance(exc, PERMANENT_ERRORS):
        return False
    if isinstance(exc, requests.HTTPError):
        status = exc.response.status_code if exc.response is not None else 0
        return not (400 <= status < 500 and status not in TRANSIENT_CLIENT_ERRORS)
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
    url: str,
    *,
    what: str,
    timeout: int,
    retries: int,
    read: Callable[[requests.Response], T],
    stream: bool = False,
) -> T:
    """GET with retries, `read` turning the response into the result, and
    `what` naming the result in the error raised when every attempt failed.

    `raise_for_status()` comes first: without it an HTML or JSON error body
    would read fine and be handed on as if it were data. A `ValueError` from
    `read` (an unparseable body, a file that is not what was asked for) is
    retried like a 5xx. `stream` leaves the body unread for `read` to consume,
    and closes the response afterwards.
    """
    last: Exception | None = None
    for attempt in range(retries):
        try:
            if stream:
                with requests.get(url, timeout=timeout, stream=True) as resp:
                    resp.raise_for_status()
                    return read(resp)
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


def get_file(
    url: str,
    dest: str | Path,
    *,
    check: Callable[[Path], None] | None = None,
    reuse: bool = False,
    timeout: int = 300,
    retries: int = RETRIES,
    chunk: int = 1 << 20,
) -> Path:
    """Stream a large file to `dest`, never holding it in memory, and put it
    there only once it is whole (`modern_data_stack.files.replaced_when_whole`).

    `check` raises `ValueError` for a body that is not what was asked for: an
    error page served with a 200, a damaged archive. It is retried like a 5xx.

    `reuse` keeps a file already at `dest` if it passes `check`, and fetches it
    again if it does not. A cache serves whatever reached it by any route,
    including before the check existed, so that is where the check has to run.
    It is refused under fixtures: a recorded file left in the cache passes the
    same check, and a later live run would load it as live data, with nothing
    red. A fixture-aware caller reads `fixtures.path_for(url)` instead, as
    `ingest.sources.retail.retail_workbook` does.

    Under fixtures the recorded file is copied, un-gzipped as `get_bytes` does,
    and checked the same way, so an offline run refuses what a live one would.
    """
    dest = Path(dest)
    if reuse and fixtures.enabled():
        raise ValueError(
            f"get_file({url!r}, reuse=True) under {fixtures.ENV_VAR}: a recorded file "
            "cached at the live path would be served to the next live run as live data"
        )
    if reuse and dest.exists():
        try:
            if check is not None:
                check(dest)
            return dest
        except ValueError as exc:
            log.warning("%s: the copy at %s is unusable (%s); fetching it again", url, dest, exc)

    if fixtures.enabled():
        path = fixtures.path_for(url)
        with files.replaced_when_whole(dest, check=check) as out:
            if path.suffix == ".gz":
                with gzip.open(path, "rb") as src:
                    shutil.copyfileobj(src, out)
            else:
                with path.open("rb") as src:
                    shutil.copyfileobj(src, out)
        return dest

    def write(resp: requests.Response) -> Path:
        with files.replaced_when_whole(dest, check=check) as out:
            for block in resp.iter_content(chunk_size=chunk):
                out.write(block)
        return dest

    return _fetch(url, what="the file", timeout=timeout, retries=retries, read=write, stream=True)
