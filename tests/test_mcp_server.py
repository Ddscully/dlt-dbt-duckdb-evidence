"""`agent/mcp_server.py`: the loop's tools over MCP, through the SDK's in-memory client.

What they hold:

* the server lists exactly the loop's tools, schemas and all, so the two cannot
  describe a tool differently;
* a tool's note arrives as a block of its own, and a refusal as an error result;
* no connection outlives a call, and a call while a build holds the file is an
  error result, not a crash. Both are checked from a second process, because the
  lock is between processes: inside one, DuckDB refuses a second connection with
  a different configuration for a reason of its own, so a same-process check
  would pass either way.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from retail_fact import sale, warehouse

from modern_data_stack.paths import dbt_manifest_path, dbt_semantic_manifest_path

# ci.yml runs pytest before `dbt parse`, so the manifests are missing there; it
# re-runs this file after the parse, which `tests/test_workflows.py` enforces.
manifest_path = dbt_manifest_path()
semantic_manifest_path = dbt_semantic_manifest_path()
pytestmark = pytest.mark.skipif(
    not (Path(manifest_path).exists() and Path(semantic_manifest_path).exists()),
    reason="needs dbt/target/manifest.json — run `just dbt-deps` and `dbt parse` first",
)

# Coverage starts on 1 June 2021, so 2021 is covered in part and gets a note.
LINES = [
    sale("2021-06-01", "A", 2, 5.0, invoice="I1", customer="c1", country="GBR"),
    sale("2022-12-31", "A", 1, 5.0, invoice="I2", customer="c1", country="GBR"),
]


@pytest.fixture(scope="module")
def catalog():
    from agent.catalog import load_catalog

    return load_catalog()


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "warehouse.duckdb"
    warehouse(LINES, path).close()
    return path


def _session(server, calls):
    """Run `calls(client)` against `server` in memory, and return what it returns."""
    import anyio
    from mcp.client.client import Client

    async def main():
        async with Client(server) as client:
            return await calls(client)

    return anyio.run(main)


def _server(catalog, database):
    from agent.mcp_server import build_server

    return build_server(catalog, lambda: str(database))


def _texts(result) -> list[str]:
    return [block.text for block in result.content]


def test_the_server_lists_exactly_the_loops_tools(catalog, database):
    import duckdb

    from agent.tools import warehouse_tools

    async def calls(client):
        return (await client.list_tools()).tools

    listed = _session(_server(catalog, database), calls)
    with duckdb.connect() as con:
        schemas = [tool.schema["function"] for tool in warehouse_tools(con, catalog)]
    assert [(t.name, t.description, t.input_schema) for t in listed] == [
        (s["name"], s["description"], s["parameters"]) for s in schemas
    ]


def test_a_note_is_a_block_of_its_own_and_a_refusal_an_error(catalog, database):
    async def calls(client):
        return (
            await client.call_tool("query_metric", {"metrics": ["orders"], "group_by": ["year"]}),
            await client.call_tool("query_metric", {"metrics": ["x"]}),
            await client.call_tool("query_metric", {"metrics": "orders", "group_by": 5}),
        )

    answered, unknown, malformed = _session(_server(catalog, database), calls)
    text, note = _texts(answered)
    assert not answered.is_error
    assert text.splitlines()[1:4] == ["year  orders", "2021       1", "2022       1"]
    assert note.startswith("Quote this note verbatim in the answer: Partly covered: 2021 (1 Jun")
    assert unknown.is_error and len(unknown.content) == 1
    assert _texts(unknown)[0].startswith("error: unknown metric x; the metrics are ")
    # The SDK does not check arguments against the schema, so the tool does.
    assert malformed.is_error
    assert _texts(malformed) == ["error: expected a name or a list of names, not 5"]


def _writer(database: Path) -> list[str]:
    """A command that opens `database` for writing in another process."""
    return [sys.executable, "-c", f"import duckdb; duckdb.connect({str(database)!r}).close()"]


def test_no_connection_outlives_a_call(catalog, database):
    async def calls(client):
        return await client.call_tool("query_metric", {"metrics": ["orders"]})

    assert not _session(_server(catalog, database), calls).is_error
    # A build, in another process, must be able to take the file between calls.
    subprocess.run(_writer(database), check=True, timeout=60)


def test_a_call_during_a_build_is_an_error_not_a_crash(catalog, database):
    from agent.mcp_server import LOCKED

    # A writer in another process, holding the file until its stdin closes.
    hold = (
        f"import duckdb, sys; con = duckdb.connect({str(database)!r}); "
        "print('ready', flush=True); sys.stdin.read()"
    )
    build = subprocess.Popen(
        [sys.executable, "-c", hold], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    try:
        assert build.stdout is not None and build.stdout.readline() == "ready\n"

        async def calls(client):
            return await client.call_tool("query_metric", {"metrics": ["orders"]})

        result = _session(_server(catalog, database), calls)
    finally:
        build.kill()
        build.wait()
    assert result.is_error
    assert _texts(result) == [LOCKED]
