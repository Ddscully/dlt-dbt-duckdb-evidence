from __future__ import annotations

import pytest
from dlt.common.configuration.container import Container
from dlt.common.pipeline import PipelineContext


@pytest.fixture(autouse=True)
def _lakehouse_on_disk(monkeypatch: pytest.MonkeyPatch):
    """Every test's landing zone is the directory it names — never a bucket, never Postgres.

    Both of `lake.lakehouse.REMOTE_ENV_VARS` outrank the `lakehouse_dir` a test
    passes (see `data_path` and `catalog`), and `just test` loads the developer's
    `.env`, so without this a machine set up for S3 would run the suite's
    throwaway catalogs against its real bucket, and one set up for Postgres would
    run them against its real catalog — writing DuckLake tables into it. A test
    about either case sets the variable back.

    The metadata schema goes too: left behind with the catalog deleted it is
    inert, but it would be read by any test that sets the catalog itself.
    """
    from lake.lakehouse import METADATA_SCHEMA_ENV_VAR, REMOTE_ENV_VARS

    for name in (*REMOTE_ENV_VARS, METADATA_SCHEMA_ENV_VAR):
        monkeypatch.delenv(name, raising=False)


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

    Shared here because `test_asset_checks.py` and `test_definitions.py` both
    import the orchestration layer. Module scope means each test module still
    gets its own teardown.
    """
    yield
    ctx = Container()[PipelineContext]
    if ctx.is_active():
        # `PipelineContext.pipeline()` is typed as the `SupportsPipeline`
        # protocol, which does not declare `deactivate` — but the object is a
        # `Pipeline`, which does. A stub gap, not a missing method: the teardown
        # this whole fixture exists for is what proves it at runtime.
        ctx.pipeline().deactivate()  # ty: ignore[unresolved-attribute]
