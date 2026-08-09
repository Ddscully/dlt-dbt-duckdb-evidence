"""Serve recorded payloads instead of live endpoints, behind the same code path.

An ingest layer that fetches from live APIs makes CI a test of whether those APIs
happen to be up, which is not what a pull request is asking. This is the shim:
one environment variable swaps every fetch for a checked-in file, so the *whole*
pipeline — schema inference, dbt, the transforms, the checks — runs offline and
deterministically.

Two things hold it up, and both are easy to give away:

* **Fixtures sit behind the same code path, not beside it.** Record the payload
  in the format the endpoint actually serves (gzipped CSV, the raw JSON body) so
  the parsing gotchas that bite in production are exercised in CI too. A
  pre-parsed fixture tests the test.
* **Resolution is explicit, and a miss raises.** Falling back to the network on
  an unmapped URL turns "offline CI" into "CI that's online sometimes", which is
  the failure you don't find until it's flaky.

The routes come from the project — `ingest/fixtures.py` has this one's.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

Route = tuple[re.Pattern[str], str]

DEFAULT_ENV_VAR = "INGEST_FIXTURES"

_TRUE = {"1", "true", "yes"}


def enabled(env_var: str = DEFAULT_ENV_VAR) -> bool:
    """True when the pipeline should read fixtures instead of the network."""
    return os.environ.get(env_var, "").lower() in _TRUE


def resolve(url: str, routes: list[Route], fixture_dir: Path) -> Path:
    """Map a source URL to its fixture file.

    `routes` is `(pattern, filename template)` pairs; named groups in the pattern
    are formatted into the template, which is how one route can cover a whole
    family of per-parameter requests.

    Raises `KeyError` rather than returning None — an unmapped URL means the
    fixture set has drifted from the pipeline, and that should stop CI rather
    than quietly reach for the network.
    """
    for pattern, template in routes:
        match = pattern.search(url)
        if match:
            return fixture_dir / template.format(**match.groupdict())
    raise KeyError(f"no fixture mapped for {url!r}")
