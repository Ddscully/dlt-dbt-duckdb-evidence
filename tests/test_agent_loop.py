"""The agent loop (`agent/loop.py`) against a scripted model, so no server is needed.

What is tested is what the loop promises whatever the model does: a figure no
tool printed is flagged, the alignment note reaches the answer verbatim, and a
tool's refusal goes back to the model rather than ending the run. The last tests
run the real `explain_change` tool, on a fact small enough to build here, so a
schema and a handler that disagree fail without a server, and so does a
connection held while the model writes. How good a given model's prose is stays
a measurement, in `docs/FINANCE_AGENT.md`.
"""

from __future__ import annotations

import json
import subprocess
import sys
from contextlib import nullcontext

import duckdb
import pytest
from retail_fact import sale, warehouse

from agent.bridge import alignment_note, explain_change, render
from agent.loop import NoAnswer, ask, unverified
from agent.tools import Tool, ToolResult, call_tool, read_only, warehouse_tools

NOTE = "Periods aligned: each year is compared over 1 Jan to 9 Dec only."
BRIDGE = "Net revenue, EUR, 2010 to 2011: €10,744.6k to €10,401.3k (-3.19%, -€343.3k)."


def stub_tool(result: ToolResult) -> Tool:
    schema = {"type": "function", "function": {"name": "explain_change", "parameters": {}}}

    def run(args: dict) -> ToolResult:
        if args["year_a"] == 2008:
            raise ValueError("2008 is outside the data")
        return result

    return Tool(schema, run)


class Scripted:
    """A `Chat` that returns its replies in order and keeps what it was sent."""

    def __init__(self, *replies: dict):
        self.replies = replies
        self.sent: list[list[dict]] = []

    def __call__(self, messages: list[dict], tools: list[dict]) -> dict:
        self.sent.append(list(messages))
        return self.replies[len(self.sent) - 1]


def call(call_id: str, **args) -> dict:
    function = {"name": "explain_change", "arguments": json.dumps(args)}
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": call_id, "function": function}],
    }


def reply(text: str) -> dict:
    return {"role": "assistant", "content": text}


def test_a_figure_no_tool_printed_is_flagged():
    # The invented sum is the smoke test's: four bars "roughly -7 pts" that total -13.4.
    text = "Revenue fell 3.19% (€343.3k), and the four falling bars came to roughly -7 pts."
    assert unverified(text, [BRIDGE]) == ("7",)
    assert unverified("1. It fell 3.19%.\n2. From €10,744.6k.", [BRIDGE]) == ()
    # By value: a shipment's cost quoted without its pennies is the tool's figure.
    assert unverified("It costs €56,156.", ["€56,156.00 for 500 t"]) == ()
    assert unverified("It costs €56,157.", ["€56,156.00 for 500 t"]) == ("56157",)

    chat = Scripted(call("c1", year_a=2010, year_b=2011), reply(text))
    answer = ask("Why did revenue fall?", chat, [stub_tool(ToolResult(BRIDGE))])
    assert str(answer).endswith("Not in any tool output, so check before quoting: 7.")


def test_the_note_is_appended_when_the_model_leaves_it_out():
    chat = Scripted(call("c1", year_a=2010, year_b=2011), reply("Revenue fell 3.19%."))
    answer = ask("Why did revenue fall?", chat, [stub_tool(ToolResult(BRIDGE, NOTE))])
    assert answer.notes == (NOTE,)
    assert str(answer).endswith(NOTE)
    assert answer.unverified == ()

    quoted = Scripted(call("c1", year_a=2010, year_b=2011), reply(f"It fell. {NOTE}"))
    assert ask("Why?", quoted, [stub_tool(ToolResult(BRIDGE, NOTE))]).notes == ()


def test_a_refusal_goes_back_to_the_model_against_its_call():
    chat = Scripted(call("c1", year_a=2008, year_b=2009), reply("2008 is outside the data."))
    answer = ask("And 2008?", chat, [stub_tool(ToolResult(BRIDGE))])
    tool_message = chat.sent[1][-1]
    assert tool_message == {
        "role": "tool",
        "tool_call_id": "c1",
        "content": "error: 2008 is outside the data",
    }
    assert answer.text == "2008 is outside the data."


def test_a_model_that_never_answers_is_stopped():
    chat = Scripted(*(call(f"c{n}", year_a=2010, year_b=2011) for n in range(3)))
    with pytest.raises(NoAnswer):
        ask("Why?", chat, [stub_tool(ToolResult(BRIDGE))], max_rounds=3)


def test_the_real_tool_runs_from_what_a_model_sends():
    # The data ends 2022-12-09, so the two years are aligned and the note is due.
    con = warehouse([sale("2021-03-01", "A", 10, 10.0), sale("2022-12-09", "A", 12, 12.0)])
    bridge = explain_change(con, 2021, 2022)
    chat = Scripted(
        call("c1", year_a="2021"),  # a missing year goes back as an error, for a retry
        call("c2", year_a="2021", year_b="2022"),  # years as strings, as small models send
        reply(f"Revenue changed by {float(bridge.change_pct):+.2%}."),
    )
    answer = ask("How did revenue change?", chat, warehouse_tools(lambda: nullcontext(con)))
    assert chat.sent[1][-1]["content"] == "error: explain_change needs year_b"
    assert chat.sent[2][-1]["content"] == render(bridge)
    assert answer.notes == (alignment_note(bridge),)
    assert answer.unverified == ()


@pytest.mark.parametrize(
    ("arguments", "refusal"),
    [
        ("[]", "error: arguments is a JSON object of named arguments, not []"),
        ("null", "error: explain_change needs year_a and year_b"),
        # Not a bridge of zeros under a note about alignment.
        ('{"year_a": 2021, "year_b": 2021}', "error: year_a and year_b are both 2021"),
    ],
)
def test_a_call_no_tool_can_answer_is_a_refusal_not_a_crash(arguments, refusal):
    con = warehouse([sale("2021-03-01", "A", 10, 10.0), sale("2022-12-09", "A", 12, 12.0)])
    tools = {tool.name: tool for tool in warehouse_tools(lambda: nullcontext(con))}
    assert call_tool(tools, "explain_change", arguments).text.startswith(refusal)
    # Null for an argument left out is the default, as small models send it.
    defaulted = call_tool(
        tools, "explain_change", '{"year_a": 2021, "year_b": 2022, "currency": null}'
    )
    assert defaulted.text == render(explain_change(con, 2021, 2022, "EUR"))


def test_an_error_from_the_warehouse_goes_back_to_the_model():
    # A warehouse that was never built: DuckDB's own error, relayed, not a traceback.
    empty = duckdb.connect()
    tools = {tool.name: tool for tool in warehouse_tools(lambda: nullcontext(empty))}
    result = call_tool(tools, "explain_change", {"year_a": 2021, "year_b": 2022})
    assert result.text.startswith("error: Catalog Error: Table with name")


def test_no_connection_is_held_while_the_model_writes(tmp_path):
    path = tmp_path / "warehouse.duckdb"
    warehouse([sale("2021-03-01", "A", 10, 10.0), sale("2022-03-01", "A", 12, 12.0)], path).close()
    build = [sys.executable, "-c", f"import duckdb; duckdb.connect({str(path)!r}).close()"]

    class Building(Scripted):
        """A model slow enough that a build starts, in another process, as it writes."""

        def __call__(self, messages: list[dict], tools: list[dict]) -> dict:
            subprocess.run(build, check=True, timeout=60)
            return super().__call__(messages, tools)

    chat = Building(call("c1", year_a=2021, year_b=2022), reply("It rose."))
    ask("How did revenue change?", chat, warehouse_tools(read_only(lambda: str(path))))
    # The second round trip came after the tool ran, and the build still got the file.
    assert chat.sent[1][-1]["content"].startswith("Net revenue, EUR, 2021 to 2022")
