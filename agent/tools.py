"""The analyst's tools over the marts: one list, used by the loop and the MCP server.

Each tool is a JSON Schema, which a model reads to choose a call, and a function
that runs it on a read-only connection. The schemas' choices (models, metrics,
groupings) are read from the dbt manifests, so a metric added in yml is offered
without a code change; `agent/loop.py` sends them to an OpenAI-compatible model,
and `agent/mcp_server.py` serves the same schemas to an MCP client, so the two
cannot describe a tool differently.

A tool refuses a call it cannot answer with a `ValueError` or `TypeError`, which
`call_tool` returns as text beginning `error:`: the caller is a model, and it
can relay the refusal or retry with the arguments corrected. Nothing checks the
arguments against the schema first (the MCP SDK does not either), so each tool
checks its own.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

import duckdb

from agent import metrics
from agent.bridge import AMOUNT_COLUMNS, alignment_note, explain_change, render
from agent.catalog import Catalog, describe_model

INSTRUCTIONS = (
    "You answer questions about a UK online retailer's revenue, from its invoices. "
    "Call a tool for every figure. Write each figure exactly as the tool printed it: "
    "do not add figures together, round them, or convert their units. If no tool gives "
    "a figure the question needs, say so rather than estimate it. Keep the answer short. "
    "Use describe_model for what a table's columns mean and which may be summed, "
    "query_metric for totals and breakdowns, and explain_change for why revenue moved "
    "between two years."
)


@dataclass(frozen=True)
class ToolResult:
    text: str  # what the model reads
    note: str | None = None  # what the answer must carry verbatim, whatever the model writes


@dataclass(frozen=True)
class Tool:
    schema: dict  # the OpenAI `tools` entry
    run: Callable[[dict], ToolResult]

    @property
    def name(self) -> str:
        return self.schema["function"]["name"]


def warehouse_tools(con: duckdb.DuckDBPyConnection, catalog: Catalog | None = None) -> list[Tool]:
    """The tools over the marts, bound to one read-only connection.

    `describe_model` and `query_metric` need the dbt manifests, so they are
    offered only with a `catalog`. Their schemas' choices are read from the
    manifests, so a metric added in yml is offered without a code change.
    """

    def run_explain_change(args: dict) -> ToolResult:
        missing = sorted({"year_a", "year_b"} - set(args))
        if missing:
            raise TypeError(f"explain_change needs {' and '.join(missing)}")
        bridge = explain_change(
            con, int(args["year_a"]), int(args["year_b"]), str(args.get("currency", "EUR"))
        )
        return ToolResult(render(bridge), alignment_note(bridge))

    explain_change_schema = {
        "type": "function",
        "function": {
            "name": "explain_change",
            "description": (
                "Explain why net revenue changed between two calendar years, as a bridge: "
                "volume, mix and price on SKUs sold in both years, new and discontinued "
                "SKUs, returns, and FX. Years the data covers only in part are compared "
                "over the days both cover, and the output says so."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "year_a": {"type": "integer", "description": "the earlier year"},
                    "year_b": {"type": "integer", "description": "the later year"},
                    "currency": {"type": "string", "enum": sorted(AMOUNT_COLUMNS)},
                },
                "required": ["year_a", "year_b"],
            },
        },
    }
    tools = [Tool(explain_change_schema, run_explain_change)]
    if catalog is not None:
        tools += _catalog_tools(con, catalog)
    return tools


def _names(value) -> list[str]:
    """`"year"` or `["year"]` as a list: small models send a lone name bare."""
    if isinstance(value, str):
        names = [value]
    elif isinstance(value, list | tuple):
        names = list(value)
    else:
        raise TypeError(f"expected a name or a list of names, not {value!r}")
    if not all(isinstance(name, str) for name in names):
        raise ValueError(f"expected names as strings, not {value!r}")
    return names


def _years(value) -> tuple[int, int] | None:
    """`2011`, `[2011]` or `[2010, 2011]`, as a (first, last) pair."""
    if value is None:
        return None
    years = [value] if isinstance(value, int | str) else list(value)
    try:
        if len(years) not in (1, 2):
            raise ValueError
        return int(years[0]), int(years[-1])
    except ValueError:
        raise ValueError(f"years is [first, last] or a single year, not {value!r}") from None


def _catalog_tools(con: duckdb.DuckDBPyConnection, catalog: Catalog) -> list[Tool]:
    layer = catalog.layer

    def run_describe_model(args: dict) -> ToolResult:
        if "model" not in args:
            raise TypeError("describe_model needs model")
        return ToolResult(describe_model(catalog, str(args["model"])))

    def run_query_metric(args: dict) -> ToolResult:
        if not args.get("metrics"):
            raise TypeError("query_metric needs metrics")
        where = args.get("where") or None
        if where is not None and not isinstance(where, dict):
            raise ValueError(
                f'where is an object, e.g. {{"region": "North America"}}, not {where!r}'
            )
        table = metrics.query_metric(
            con,
            layer,
            _names(args["metrics"]),
            group_by=_names(args.get("group_by") or []),
            years=_years(args.get("years")),
            where=where,
        )
        return ToolResult(metrics.render(table), metrics.coverage_note(table))

    describe_model_schema = {
        "type": "function",
        "function": {
            "name": "describe_model",
            "description": (
                "Describe a warehouse table: what one row is, what it means, which "
                "metrics read it, and each column's type and whether summing it means "
                "anything."
            ),
            "parameters": {
                "type": "object",
                "properties": {"model": {"type": "string", "enum": list(catalog.describable)}},
                "required": ["model"],
            },
        },
    }
    metric_list = "; ".join(
        f"{name}: {' '.join(spec.get('description', '').split())}"
        for name, spec in sorted(layer.metrics.items())
    )
    query_metric_schema = {
        "type": "function",
        "function": {
            "name": "query_metric",
            "description": (
                "Totals of retail metrics, optionally broken down by period or place, "
                "with an 'all' row. Metrics: " + metric_list
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "metrics": {
                        "type": "array",
                        "items": {"type": "string", "enum": sorted(layer.metrics)},
                    },
                    "group_by": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(metrics.GROUP_BY)},
                        "description": "at most one of year, quarter and month",
                    },
                    "years": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "[first, last] calendar years, inclusive",
                    },
                    "where": {
                        "type": "object",
                        "properties": {name: {"type": "string"} for name in metrics.FILTERS},
                        "description": "an exact country name or World Bank region",
                    },
                },
                "required": ["metrics"],
            },
        },
    }
    return [
        Tool(describe_model_schema, run_describe_model),
        Tool(query_metric_schema, run_query_metric),
    ]


def call_tool(tools: dict[str, Tool], name: str, arguments: str | dict) -> ToolResult:
    """Run one tool call. A refusal goes back to the model as text, to relay or retry."""
    if name not in tools:
        return ToolResult(f"error: no tool named {name!r}; the tools are {', '.join(tools)}")
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
        return tools[name].run(args)
    except (ValueError, TypeError) as exc:
        return ToolResult(f"error: {exc}")
