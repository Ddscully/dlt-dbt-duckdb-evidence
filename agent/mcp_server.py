"""The analyst's tools, served over the Model Context Protocol to any MCP client.

`agent/loop.py` is the agent for a local model; this serves the same tools
(`explain_change`, `describe_model`, `query_metric`, `run_scenario`) to someone
else's agent — Claude Code, Claude Desktop, an IDE. The client launches it and speaks to it on
stdin and stdout, so nothing here listens on a port, and **nothing may print to
stdout**: it is the protocol channel.

* **The schemas are the loop's.** Each tool's `input_schema` is the `parameters`
  of its entry in `agent/tools.py`, sent unchanged, so the two cannot describe a
  tool differently. The SDK does not check arguments against the schema; each
  tool checks its own, and a refusal comes back as an error result for the
  client's model to relay or retry.
* **A read-only connection per call, never one held.** A server lives as long as
  the client's session, and DuckDB takes one writer or many readers across
  processes: a connection held between calls would make every build fail for as
  long as the client stayed open. Opening one costs a fraction of a second. A
  call made while a build holds the file gets an error result saying so. The
  tools open and close it themselves (`agent/tools.py`), as they do for the loop.
* **The tools are built once, at start**, and the metrics read then, so restart
  the server after `just dbt-parse`.

What the loop guarantees and this cannot: the loop appends each tool's note to
the answer verbatim, and flags any number in the answer that no tool printed.
Here the client's model writes the answer, so the note is sent as a second block
that asks to be quoted, and nothing checks the prose.

Run:  uv run python -m agent.mcp_server
"""

from __future__ import annotations

import sys
from collections.abc import Callable

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from agent.catalog import Catalog, load_catalog
from agent.tools import INSTRUCTIONS, call_tool, read_only, warehouse_tools
from modern_data_stack.paths import warehouse_path

NOTE = "Quote this note verbatim in the answer: "


def build_server(catalog: Catalog, database: Callable[[], str] = warehouse_path) -> Server:
    """The server, calling `database()` for the file to open on each call."""
    tools = {tool.name: tool for tool in warehouse_tools(read_only(database), catalog)}
    schemas = [tool.schema["function"] for tool in tools.values()]

    async def list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=schema["name"],
                    description=schema["description"],
                    input_schema=schema["parameters"],
                )
                for schema in schemas
            ]
        )

    async def run_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        result = call_tool(tools, params.name, params.arguments)
        content = [types.TextContent(type="text", text=result.text)]
        if result.note:
            content.append(types.TextContent(type="text", text=NOTE + result.note))
        return types.CallToolResult(content=content, is_error=result.text.startswith("error:"))

    return Server(
        "warehouse", instructions=INSTRUCTIONS, on_list_tools=list_tools, on_call_tool=run_tool
    )


async def _serve(server: Server) -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    try:
        catalog = load_catalog()
    except FileNotFoundError as exc:
        sys.exit(str(exc))
    anyio.run(_serve, build_server(catalog))


if __name__ == "__main__":
    main()
