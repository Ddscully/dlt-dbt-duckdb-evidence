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
checks its own. An error from DuckDB comes back the same way, since a traceback
ends the loop's run and reaches an MCP client as a bare `Internal server error`.

**A tool opens the warehouse for its own call and closes it**, through the
`Connect` it was built with. DuckDB takes one writer or many readers across
processes, and both callers outlive a call by minutes: the loop waits on a
model between calls, and the server lives as long as its client. A connection
held across either would make every build fail for that long. A call made
while a build holds the file gets `LOCKED` back.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass

import duckdb

from agent import metrics, scenario
from agent.bridge import AMOUNT_COLUMNS, alignment_note, explain_change, render
from agent.catalog import Catalog, describe_model

INSTRUCTIONS = (
    "You answer questions about a UK online retailer's revenue, from its invoices, and "
    "about the EU carbon border cost (CBAM) of imported steel, aluminium, cement, "
    "fertilisers and hydrogen. "
    "Call a tool for every figure. Write each figure exactly as the tool printed it: "
    "do not add figures together, round them, or convert their units. If no tool gives "
    "a figure the question needs, say so rather than estimate it. Keep the answer short. "
    "Use describe_model for what a table's columns mean and which may be summed, "
    "query_metric for totals and breakdowns, explain_change for why revenue moved "
    "between two years, and run_scenario for what CBAM costs per tonne of a good at a "
    "carbon price."
)

LOCKED = "error: the warehouse is locked, most likely by a build; try again when it finishes"
# DuckDB's words for the lock. Its message quotes the file's path too, so a bare
# "lock" would also match a missing warehouse under a directory named `blocks`.
_LOCK_MESSAGE = "Could not set lock on file"

# Opens the warehouse for one tool call; leaving the `with` closes it.
Connect = Callable[[], AbstractContextManager[duckdb.DuckDBPyConnection]]


def read_only(database: Callable[[], str]) -> Connect:
    """A `Connect` that opens `database()` read-only, afresh for every call."""
    return lambda: duckdb.connect(database(), read_only=True)


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


def warehouse_tools(connect: Connect, catalog: Catalog | None = None) -> list[Tool]:
    """The tools over the marts, each opening the warehouse with `connect` when it runs.

    Built once: nothing here touches the warehouse until a tool is called.
    `describe_model` and `query_metric` need the dbt manifests, so they are
    offered only with a `catalog`. Their schemas' choices are read from the
    manifests, so a metric added in yml is offered without a code change.
    """

    def run_explain_change(args: dict) -> ToolResult:
        missing = sorted({"year_a", "year_b"} - set(args))
        if missing:
            raise TypeError(f"explain_change needs {' and '.join(missing)}")
        # `or`, not a default: a small model sends null for an argument it leaves out.
        currency = str(args.get("currency") or "EUR")
        with connect() as con:
            bridge = explain_change(con, int(args["year_a"]), int(args["year_b"]), currency)
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

    def run_run_scenario(args: dict) -> ToolResult:
        missing = sorted({"good", "year", "ets_price_eur_per_t"} - set(args))
        if missing:
            raise TypeError(f"run_scenario needs {' and '.join(missing)}")
        with connect() as con:
            result = scenario.run_scenario(
                con,
                str(args["good"]),
                args["year"],
                args["ets_price_eur_per_t"],
                countries=_names(args.get("countries") or []),
                tonnes=args.get("tonnes"),
            )
        return ToolResult(scenario.render(result), scenario.scenario_note(result))

    run_scenario_schema = {
        "type": "function",
        "function": {
            "name": "run_scenario",
            "description": (
                "What CBAM, the EU carbon border tax, costs per tonne of one imported good "
                "at a carbon price you choose: the cheapest, median and dearest source the "
                "regulation lists, its fallback for other countries, and any countries "
                "named. The figures are gross, before the free-allocation deduction. A "
                "term that matches several goods returns them to choose from."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "good": {
                        "type": "string",
                        "description": "a CN code, a good_key, or words from the good's description",
                    },
                    "year": {"type": "integer", "enum": list(scenario.YEARS)},
                    "ets_price_eur_per_t": {
                        "type": "number",
                        "description": "the carbon price to assume, EUR per tonne of CO2e",
                    },
                    "countries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "countries of origin to show, by name or ISO3 code",
                    },
                    "tonnes": {
                        "type": "number",
                        "description": "tonnes of the good, to cost a shipment",
                    },
                },
                "required": ["good", "year", "ets_price_eur_per_t"],
            },
        },
    }
    tools = [Tool(explain_change_schema, run_explain_change)]
    if catalog is not None:
        tools += _catalog_tools(connect, catalog)
    return [*tools, Tool(run_scenario_schema, run_run_scenario)]


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
    if not years:  # an empty list is no years, as null is
        return None
    try:
        if len(years) not in (1, 2):
            raise ValueError
        return int(years[0]), int(years[-1])
    except ValueError:
        raise ValueError(f"years is [first, last] or a single year, not {value!r}") from None


def _catalog_tools(connect: Connect, catalog: Catalog) -> list[Tool]:
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
        with connect() as con:
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


def call_tool(tools: dict[str, Tool], name: str, arguments: str | dict | None) -> ToolResult:
    """Run one tool call. A refusal goes back to the model as text, to relay or retry."""
    if name not in tools:
        return ToolResult(f"error: no tool named {name!r}; the tools are {', '.join(tools)}")
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
        if args is None:  # null for no arguments: the tool then names the ones it needs
            args = {}
        if not isinstance(args, dict):
            raise TypeError(f"arguments is a JSON object of named arguments, not {args!r}")
        return tools[name].run(args)
    except (ValueError, TypeError) as exc:
        return ToolResult(f"error: {exc}")
    except duckdb.Error as exc:
        # A missing file raises the lock's own class; only the lock is worth a retry.
        return ToolResult(LOCKED if _LOCK_MESSAGE in str(exc) else f"error: {exc}")
