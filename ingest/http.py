"""Shared HTTP fetching for the ingest layer.

Two functions, both fixture-aware: `INGEST_FIXTURES=1` routes them through
`ingest.fixtures` instead of the network, which is what makes CI offline.

**Call these as `http.get_json(...)`, never `from ingest.http import get_json`.**
`tests/test_ingest.py` patches `setattr(http, "get_json", ...)`; a name bound
into a source module would not be looked up through that patch, and the test
would pass while exercising the real fetch path.
"""

from __future__ import annotations

import gzip
import json
import time

import requests

from ingest import fixtures


def get_json(url: str, *, timeout: int = 120, retries: int = 3) -> dict | list:
    """GET + parse JSON with a few retries — the World Bank & Eurostat APIs
    occasionally return a transient error page or non-JSON body.

    A non-2xx status is retried and ultimately raised: without the
    `raise_for_status()` an HTML/JSON error body would parse fine and be handed
    on as if it were data.
    """
    if fixtures.enabled():
        path = fixtures.path_for(url)
        # The FX fixture is gzipped: it is kept whole, because the models are
        # tested against its discontinuities, and whole it is 3.6 MB of JSON.
        if path.suffix == ".gz":
            return json.loads(gzip.decompress(path.read_bytes()))
        return json.loads(path.read_text())

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


def get_json_object(url: str, *, timeout: int = 120, retries: int = 3) -> dict:
    """`get_json` for an endpoint that documents a JSON *object*.

    `get_json` returns `dict | list` because the World Bank sends
    `[metadata, [records…]]`; its callers narrow that by hand. Eurostat's
    JSON-stat and the ECB's `{"rates": …}` are objects, and this narrows once for
    them — an error object served with a 200 is something these APIs do.
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
