"""Ask a model a question about revenue, and let it answer only with the tools' figures.

The model chooses which tool to call and writes the prose; the tools do every
calculation. Three things keep the answer honest, each because a model was
measured doing without it:

* **Figures come from tools.** The system prompt says to quote them as printed,
  and `render` prints every subtotal a reader would want, so nothing needs
  adding up. An 8B model given the bars alone summed them itself, and wrongly.
* **The alignment note is appended by the loop, verbatim.** No model tried
  repeated it when told to, so an answer comparing two years of different
  coverage would otherwise lose the one sentence that says the naive figure
  was different.
* **Every number in the answer is checked against the tool output.** A number
  that appears in no tool output, and not in the question, is listed under the
  answer as unverified. The check is on digits, so a model that writes
  "two-thirds" gets past it, and one that rounds €2,227.6k to €2.2m is flagged
  for it: quoting exactly is the instruction. Prose is not checked at all: a
  model has written "the full calendar years" above a note saying 1–31
  December, and a figure copied onto the wrong bar passes too.

It speaks the OpenAI chat-completions API, which Ollama serves at `/v1`, as do
vLLM, LiteLLM and the hosted APIs; the endpoint and model are configuration.
Nothing here is specific to one server.

Run:  uv run python -m agent.loop "Why did revenue fall from 2010 to 2011?"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import duckdb

from agent import metrics
from agent.bridge import AMOUNT_COLUMNS, alignment_note, explain_change, render
from agent.catalog import Catalog, describe_model, load_catalog
from modern_data_stack.paths import warehouse_path

BASE_URL = "http://localhost:11434/v1"  # Ollama's OpenAI-compatible endpoint
MODEL = "granite4.1:8b"
MAX_ROUNDS = 5

SYSTEM = (
    "You answer questions about a UK online retailer's revenue, from its invoices. "
    "Call a tool for every figure. Write each figure exactly as the tool printed it: "
    "do not add figures together, round them, or convert their units. If no tool gives "
    "a figure the question needs, say so rather than estimate it. Keep the answer short. "
    "Use describe_model for what a table's columns mean and which may be summed, "
    "query_metric for totals and breakdowns, and explain_change for why revenue moved "
    "between two years."
)

Message = dict
# One round trip: the conversation so far and the tool schemas in, the model's message out.
Chat = Callable[[list[Message], list[dict]], Message]


class NoAnswer(RuntimeError):
    """The model kept calling tools and never answered."""


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


@dataclass(frozen=True)
class Answer:
    text: str
    notes: tuple[str, ...]
    unverified: tuple[str, ...]
    calls: tuple[str, ...]

    def __str__(self) -> str:
        parts = [self.text.strip(), *self.notes]
        if self.unverified:
            parts.append(
                "Not in any tool output, so check before quoting: "
                + ", ".join(self.unverified)
                + "."
            )
        return "\n\n".join(parts)


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
    names = [value] if isinstance(value, str) else list(value)
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


def _call(tools: dict[str, Tool], name: str, arguments: str | dict) -> ToolResult:
    """Run one tool call. A refusal goes back to the model as text, to relay or retry."""
    if name not in tools:
        return ToolResult(f"error: no tool named {name!r}; the tools are {', '.join(tools)}")
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
        return tools[name].run(args)
    except (ValueError, TypeError) as exc:
        return ToolResult(f"error: {exc}")


# A number is digits with optional thousands commas and decimals. A list
# marker at the start of a line ("1. ") is layout, not a figure.
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_LIST_MARKER = re.compile(r"^\s*\d+[.)]\s", re.MULTILINE)


def _numbers(text: str) -> list[str]:
    found = []
    for token in _NUMBER.findall(_LIST_MARKER.sub("", text)):
        token = token.replace(",", "")
        found.append(token if "." in token else str(int(token)))
    return found


def unverified(answer: str, sources: Sequence[str]) -> tuple[str, ...]:
    """The numbers in `answer` that appear in none of `sources`, in order, once each.

    Signs are ignored ("fell 3.19%" quotes "-3.19%"), and so is the unit, so a
    figure copied onto the wrong bar passes: this catches invented arithmetic,
    not misattribution.
    """
    known = {number for source in sources for number in _numbers(source)}
    return tuple(dict.fromkeys(n for n in _numbers(answer) if n not in known))


def ask(question: str, chat: Chat, tools: Sequence[Tool], max_rounds: int = MAX_ROUNDS) -> Answer:
    """Run the loop until the model answers without calling a tool."""
    by_name = {tool.name: tool for tool in tools}
    schemas = [tool.schema for tool in tools]
    messages: list[Message] = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": question},
    ]
    outputs: list[str] = []
    notes: dict[str, None] = {}  # ordered, and one copy of a note two calls both return
    calls: list[str] = []
    for _ in range(max_rounds):
        message = chat(messages, schemas)
        messages.append(message)
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            text = message.get("content") or ""
            return Answer(
                text=text,
                notes=tuple(note for note in notes if note not in text),
                unverified=unverified(text, [question, *outputs]),
                calls=tuple(calls),
            )
        for call in tool_calls:
            function = call["function"]
            calls.append(f"{function['name']}({function['arguments']})")
            result = _call(by_name, function["name"], function["arguments"])
            outputs.append(result.text)
            if result.note:
                notes[result.note] = None
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": result.text})
    raise NoAnswer(f"no answer after {max_rounds} rounds of tool calls")


def openai_chat(base_url: str, model: str, api_key: str | None = None) -> Chat:
    """A `Chat` against any OpenAI-compatible `/chat/completions` endpoint."""
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    def chat(messages: list[Message], tools: list[dict]) -> Message:
        body = {"model": model, "messages": messages, "tools": tools, "temperature": 0}
        request = urllib.request.Request(url, json.dumps(body).encode(), headers)
        # A local model can take minutes, not seconds.
        with urllib.request.urlopen(request, timeout=600) as response:
            return json.load(response)["choices"][0]["message"]

    return chat


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question")
    parser.add_argument("--base-url", default=os.environ.get("AGENT_BASE_URL", BASE_URL))
    parser.add_argument("--model", default=os.environ.get("AGENT_MODEL", MODEL))
    args = parser.parse_args()
    chat = openai_chat(args.base_url, args.model, os.environ.get("AGENT_API_KEY"))
    try:
        catalog = load_catalog()
    except FileNotFoundError as exc:
        sys.exit(str(exc))
    try:
        # Read-only: fails while a build holds the file, by DuckDB's design.
        with duckdb.connect(warehouse_path(), read_only=True) as con:
            answer = ask(args.question, chat, warehouse_tools(con, catalog))
    except urllib.error.HTTPError as exc:  # the server's reason, e.g. a model not pulled
        sys.exit(f"{args.base_url} refused: {exc.code} {exc.read().decode(errors='replace')}")
    except urllib.error.URLError as exc:
        sys.exit(f"cannot reach {args.base_url}: {exc.reason}. Is the model server running?")
    except NoAnswer as exc:
        sys.exit(f"{args.model}: {exc}")
    for call in answer.calls:
        print(f"called {call}", file=sys.stderr)
    print(answer)


if __name__ == "__main__":
    main()
