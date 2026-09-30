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
from decimal import Decimal
from pathlib import Path

from agent.catalog import load_catalog
from agent.tools import INSTRUCTIONS, Tool, call_tool, read_only, warehouse_tools
from modern_data_stack.paths import warehouse_path

BASE_URL = "http://localhost:11434/v1"  # Ollama's OpenAI-compatible endpoint
MODEL = "granite4.1:8b"
MAX_ROUNDS = 5
TIMEOUT = 600  # seconds for one reply: a local model can take minutes, not seconds

SYSTEM = INSTRUCTIONS

Message = dict
# One round trip: the conversation so far and the tool schemas in, the model's message out.
Chat = Callable[[list[Message], list[dict]], Message]


class NoAnswer(RuntimeError):
    """No answer came back: the model kept calling tools, or a reply held no message."""


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


# A number is digits with optional thousands commas and decimals. A list
# marker at the start of a line ("1. ") is layout, not a figure.
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_LIST_MARKER = re.compile(r"^\s*\d+[.)]\s", re.MULTILINE)


def _numbers(text: str) -> list[str]:
    found = []
    for token in _NUMBER.findall(_LIST_MARKER.sub("", text)):
        # By value, so a model's "€56,156" quotes the tool's "€56,156.00".
        found.append(f"{Decimal(token.replace(',', '')).normalize():f}")
    return found


def unverified(answer: str, sources: Sequence[str]) -> tuple[str, ...]:
    """The numbers in `answer` that appear in none of `sources`, in order, once each.

    Signs are ignored ("fell 3.19%" quotes "-3.19%"), numbers compare by value
    ("56,156" quotes "56,156.00"), and the unit is ignored too, so a
    figure copied onto the wrong bar passes: this catches invented arithmetic,
    not misattribution.
    """
    known = {number for source in sources for number in _numbers(source)}
    return tuple(dict.fromkeys(n for n in _numbers(answer) if n not in known))


def ask(
    question: str,
    chat: Chat,
    tools: Sequence[Tool],
    max_rounds: int = MAX_ROUNDS,
    on_call: Callable[[str], None] | None = None,
) -> Answer:
    """Run the loop until the model answers without calling a tool.

    `on_call` is told each call as the model makes it, before the tool runs: a
    run that ends with no answer returns no `Answer` to read the calls from.
    """
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
            if on_call is not None:
                on_call(calls[-1])
            result = call_tool(by_name, function["name"], function["arguments"])
            outputs.append(result.text)
            if result.note:
                notes[result.note] = None
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": result.text})
    raise NoAnswer(f"no answer after {max_rounds} rounds of tool calls")


def openai_chat(
    base_url: str, model: str, api_key: str | None = None, timeout: float = TIMEOUT
) -> Chat:
    """A `Chat` against any OpenAI-compatible `/chat/completions` endpoint."""
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    def chat(messages: list[Message], tools: list[dict]) -> Message:
        body = {"model": model, "messages": messages, "tools": tools, "temperature": 0}
        request = urllib.request.Request(url, json.dumps(body).encode(), headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
        try:
            return json.loads(body)["choices"][0]["message"]
        except (ValueError, LookupError, TypeError):
            # A 200 whose body is an error, or not JSON: say what came back.
            text = body[:300].decode(errors="replace")
            raise NoAnswer(f"the reply holds no message: {text}") from None

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
    if not Path(warehouse_path()).exists():
        # Said here, not by the model after a round trip spent finding out.
        sys.exit(f"{warehouse_path()} does not exist: run `just run`")

    def called(call: str) -> None:
        # As it is made, not after the answer: a model that gives up after its
        # last round, or a server lost mid-run, leaves no answer to print them with.
        print(f"called {call}", file=sys.stderr, flush=True)

    try:
        # Each tool call opens the warehouse read-only and closes it: a connection
        # held while the model writes would make a build fail for minutes.
        tools = warehouse_tools(read_only(warehouse_path), catalog)
        answer = ask(args.question, chat, tools, on_call=called)
    except urllib.error.HTTPError as exc:  # the server's reason, e.g. a model not pulled
        sys.exit(f"{args.base_url} refused: {exc.code} {exc.read().decode(errors='replace')}")
    except urllib.error.URLError as exc:
        sys.exit(f"cannot reach {args.base_url}: {exc.reason}. Is the model server running?")
    # urllib wraps a failure to connect in URLError, and neither of these: a
    # reply that never comes, and a connection closed before one.
    except TimeoutError:
        sys.exit(f"{args.base_url} sent no reply within {TIMEOUT} seconds")
    except ConnectionError as exc:
        sys.exit(f"{args.base_url} closed the connection: {exc}")
    except NoAnswer as exc:
        sys.exit(f"{args.model}: {exc}")
    print(answer)


if __name__ == "__main__":
    main()
