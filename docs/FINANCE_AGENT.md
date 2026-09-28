# Asking a model about revenue

`just ask "…"` puts a question to a language model that has the warehouse's
analysis tools and nothing else. The model chooses a tool and its arguments and
writes the answer; the tool computes every figure in it. The one tool so far is
`explain_change`, the revenue bridge in `agent/bridge.py`: why net revenue moved
between two years, as volume, mix, price, new and discontinued SKUs, returns and
FX, in bars that sum to the change exactly
([0014](decisions/0014-the-revenue-bridge-bars.md)).

```
$ AGENT_MODEL=gemma4:26b-a4b-it-q4_K_M just ask "Why did revenue fall from 2010 to 2011, in euros?"
called explain_change({"currency":"EUR","year_a":2010,"year_b":2011})
Net revenue fell from €10,744.6k to €10,401.3k due to:
* **Continuing SKUs (sold in both years):** -€1,901.2k (driven by volume: -€2,227.6k, mix: +€590.9k, and price: -€264.5k)
* **Returns (product cancellations):** -€263.6k
* **FX (GBP to EUR rate):** -€133.3k

This was partially offset by **Catalogue churn** of +€1,954.8k (New SKUs: +€2,737.2k; Discontinued SKUs: -€782.4k).

Periods aligned: the data covers 2009-12-01 to 2011-12-09, so each year is compared over 1 Jan to 9 Dec only (2010-01-01 to 2010-12-09 and 2011-01-01 to 2011-12-09; 343 and 343 days). Over the full calendar years the change would read -6.86%, against -3.19% aligned.
```

The last paragraph is not the model's: the loop appends it
([below](#what-the-loop-guarantees-and-what-it-does-not)).
`just explain-change 2010 2011` prints the tool's own output, with no model
involved.

## Running it on a laptop, with Ollama

1. **A built warehouse.** `just run` once. The loop opens
   `data/warehouse.duckdb` read-only, so it fails while a build holds the file
   (`querying-the-warehouse`), and a build fails while it is reading.
2. **Ollama, serving on port 11434, with a model that can call tools.** As a
   container, which is how it runs on the machine the measurements below come
   from:

   ```sh
   docker run -d --gpus all -v ollama:/root/.ollama -p 11434:11434 --name ollama ollama/ollama
   docker exec ollama ollama pull granite4.1:8b
   ```

   Without an NVIDIA GPU and its container toolkit, drop `--gpus all` and the
   model runs on the CPU, slower. A native install from
   [ollama.com](https://ollama.com/download) serves the same port, and the pull
   is `ollama pull granite4.1:8b`.
3. **Ask, with the question quoted** — `?` is a glob to the shell:

   ```sh
   just ask "Why did revenue fall from 2010 to 2011?"
   ```

   Each tool call is printed to stderr as `called …`, then the answer.

A server that is not running reads `cannot reach http://localhost:11434/v1:
[Errno 111] Connection refused`, and a model not yet pulled is Ollama's own
`404 … model 'x' not found`.

## Pointing it at another server

The loop speaks the OpenAI chat-completions API, which Ollama serves at `/v1`,
so any server that speaks it is a change of configuration, set in the shell or
in `.env` (`.env.example` lists them):

| Variable | Default | |
|---|---|---|
| `AGENT_BASE_URL` | `http://localhost:11434/v1` | the endpoint, up to and including `/v1` |
| `AGENT_MODEL` | `granite4.1:8b` | the model name the server knows it by |
| `AGENT_API_KEY` | unset | sent as a bearer token, and only when set |

vLLM, a LiteLLM proxy and the hosted APIs all take that form; only Ollama has
been run against here. The model must support tool calling, and the request
asks for temperature 0, so a run can be repeated. The client is the standard
library's `urllib` rather than a vendor SDK, so the loop adds no dependency.

## What the loop guarantees, and what it does not

Three things hold whatever the model writes, each because a model was measured
doing without it. `tests/test_agent_loop.py` holds them against a scripted
model, so no server is needed, and each test goes red when its guarantee is
removed; one of them runs the real `explain_change` on a fact built in memory,
so a tool schema and its handler cannot drift apart unnoticed.

- **Figures come from tools.** The system prompt says to quote each figure as
  the tool printed it and not to add figures together, and `render` prints
  every subtotal a reader would want. An 8B model given the bars alone summed
  them itself, and wrongly.
- **The alignment note is appended by the loop, verbatim.** When a comparison
  was cut to the days both years cover, no model tried said what the full
  years would have read, including when told to. The note is added unless the
  answer already contains it.
- **Every number in the answer is checked against the tool output**, and one
  that appears in no tool output and not in the question is listed under the
  answer: `Not in any tool output, so check before quoting: …`. Signs and
  thousands separators are ignored, so "fell 3.19%" quotes "-3.19%"; a rounded
  figure (€2.2m for €2,227.6k) is flagged, which is the point of asking for
  exact quotes.

What no check here catches:

- **A real figure on the wrong bar.** The check matches digits, not what they
  are attached to.
- **Prose that contradicts the tool.** Nothing reads the words, so a figure
  written as a word ("two-thirds") and a false description of the period both
  pass.

## Measured

Warehouse built 2026-09-28; Ollama in the container above. Every run called
`explain_change` once with the right arguments, and none had a number flagged.

| Model | Question | What the answer got wrong |
|---|---|---|
| `granite4.1:8b` (5.3 GB), about a minute | 2010 → 2011, EUR | two subtotals given to one of their bars: the continuing-SKU total, −17.69 pts, as volume's, and the churn total, +€1,954.8k, as new SKUs' |
| `gemma4:26b-a4b-it-q4_K_M` (17 GB), about a minute and a half | 2010 → 2011, EUR | nothing: each subtotal with its own bars under it |
| `granite4.1:8b`, under half a minute | 2008 → 2009 | nothing: the tool's refusal relayed, that the data starts on 2009-12-01 |
| `granite4.1:8b`, under half a minute | 2009 → 2010, GBP | "the comparison covers the full calendar years", above the note saying 1–31 December. A system-prompt line telling it not to describe the period changed nothing on a re-run, and was taken out |

The two failures are the two kinds the checks cannot see, and both are the
smaller model's. To compare models on a question, run it twice with
`AGENT_MODEL` changed; the scripted-model tests are what hold the loop itself.

## Adding a tool

A `Tool` in `agent/loop.py` is its OpenAI `tools` schema and a
`run(args) -> ToolResult`. `ToolResult.text` is what the model reads, and
`ToolResult.note`, when set, is what every answer carries verbatim. Add the tool
to `warehouse_tools(con)`, and write its text the way `render` writes the
bridge: every figure a reader could want already computed and labelled, so the
model has nothing to add up. A `ValueError` or `TypeError` from `run` goes back
to the model as `error: …`, for it to relay or to retry with other arguments.
