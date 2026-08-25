from __future__ import annotations

import pytest
from dlt.common.configuration.container import Container
from dlt.common.pipeline import PipelineContext


@pytest.fixture(autouse=True, scope="module")
def _release_the_dlt_pipeline():
    """Importing `orchestration.assets` leaves a dlt pipeline *active* process-wide.

    The `@dlt_assets` decorators call `build_pipeline()` at import time, and dlt
    records the result as the ambient pipeline. Any later test that calls a
    resource generator directly — `pipeline.wb_wdi()` in `tests/test_ingest.py`
    does — then reads the real `~/.dlt` state instead of no state, so
    `wdi_start_year` returns a lookback window and the URL grows a `&date=` the
    test never asked for. It fails only when the whole suite runs, only on a
    machine that has loaded WDI at least once, and names pagination as the
    culprit.

    Shared here rather than duplicated per file: `test_asset_checks.py` and
    `test_definitions.py` both import the orchestration layer and both used to
    carry an identical copy of this fixture. Module scope means every test
    module that runs still gets its own teardown, so centralising it changes
    nothing about when the deactivation happens — only that there is one
    definition of it.
    """
    yield
    ctx = Container()[PipelineContext]
    if ctx.is_active():
        # `PipelineContext.pipeline()` is typed as the `SupportsPipeline`
        # protocol, which does not declare `deactivate` — but the object is a
        # `Pipeline`, which does. A stub gap, not a missing method: the teardown
        # this whole fixture exists for is what proves it at runtime.
        ctx.pipeline().deactivate()  # ty: ignore[unresolved-attribute]
