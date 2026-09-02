"""Shared HTTP fetching for the ingest layer.

Two functions, both fixture-aware: `INGEST_FIXTURES=1` routes them through
`ingest.fixtures` instead of the network, which is what makes CI offline.

**Call these as `http.get_json(...)`, never `from ingest.http import get_json`.**
Binding the name into a source module would make
`monkeypatch.setattr(http, "get_json", ...)` — which `tests/test_ingest.py` does
in ten places — patch a name nothing looks up any more: the test would pass
while exercising the real fetch path. Reaching through the module keeps one
patch point for all six sources, which is the behaviour they had when they
shared a file.
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
        # One fixture is gzipped — the FX series is 3.6 MB of JSON whole and
        # 831 kB compressed, and it is kept whole because every discontinuity in
        # it (see `ecb_fx_rates`) is something the models are tested against.
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

    The union `get_json` returns is honest — the World Bank really does send
    `[metadata, [records…]]` — and both World Bank callers already narrow it by
    hand, because an error object served with a 200 is a real thing those APIs
    do. This is the same check for the other branch: Eurostat's JSON-stat and
    the ECB's `{"rates": …}` are objects, and a caller that subscripts one by
    name should say so once rather than at every key.
    """
    payload = get_json(url, timeout=timeout, retries=retries)
    if not isinstance(payload, dict):
        # `RuntimeError` rather than the `TypeError` TRY004 asks for, and
        # deliberately: nothing here passed a wrong argument — the URL was fine
        # and the *server* sent the wrong shape. That is the same fault the two
        # World Bank checks below raise `RuntimeError` for; they escape the rule
        # only because their condition is compound. Matching them is worth the
        # one suppression.
        raise RuntimeError(  # noqa: TRY004
            f"expected a JSON object from {url}, got {payload!r:.300}"
        )
    return payload
