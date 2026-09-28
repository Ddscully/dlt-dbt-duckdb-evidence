# Asking a model about revenue

`just ask "…"` puts a question to a language model that has the warehouse's
analysis tools and nothing else. The model chooses a tool and its arguments and
writes the answer; the tools compute every figure in it. There are three:

- **`explain_change`**, the revenue bridge in `agent/bridge.py`: why net revenue
  moved between two years, as volume, mix, price, new and discontinued SKUs,
  returns and FX, in bars that sum to the change exactly
  ([0014](decisions/0014-the-revenue-bridge-bars.md)).
- **`query_metric`** (`agent/metrics.py`): totals and breakdowns of the retail
  metrics defined in dbt's semantic layer, by year, quarter, month, country or
  region, with an `all` row ([below](#the-metrics)).
- **`describe_model`** (`agent/catalog.py`): what one row of a retail mart is,
  and each column's type, additivity label and description, read from the dbt
  manifest, so a model can tell `quantity` (sum it) from `unit_price` (never).

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
`just explain-change 2010 2011`, `just metric …` and `just describe-model …`
print the tools' own output, with no model involved, and `just mcp-server`
serves the same three tools to an MCP client such as Claude Code or Claude
Desktop ([below](#from-an-mcp-client)).

## Running it on a laptop, with Ollama

1. **A built warehouse.** `just run` once. The loop opens
   `data/warehouse.duckdb` read-only, so it fails while a build holds the file
   (`querying-the-warehouse`), and a build fails while it is reading. It also
   reads `dbt/target/manifest.json` and `semantic_manifest.json`, which every
   dbt command writes; without them it stops and says to run `just dbt-parse`.
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

## From an MCP client

`agent/mcp_server.py` serves the three tools over the Model Context Protocol, on
stdio: the client launches it, and nothing listens on a port
([0016](decisions/0016-the-mcp-server.md)). The warehouse and the dbt manifests
are needed as for the loop, and the `dev` dependency group, which `just setup`
installs. In Claude Code, from the repo root:

```sh
claude mcp add warehouse -- uv run --directory "$PWD" python -m agent.mcp_server
```

That registers it for you in this directory only (`--scope local`, the
default), and writes nothing into the repo. The tools appear as
`mcp__warehouse__query_metric` and so on. In Claude Desktop, the same command
goes in `claude_desktop_config.json`, with the repo's absolute path:

```json
{
  "mcpServers": {
    "warehouse": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/repo", "python", "-m", "agent.mcp_server"]
    }
  }
}
```

The server finds `data/warehouse.duckdb` from the package's location, whatever
directory the client starts it in, and `WAREHOUSE_PATH` points it elsewhere.
Restart it after `just dbt-parse`: it reads the metrics once, at start.

**What holds, compared with `just ask`.** The figures and the refusals are the
same tools', with the same schemas: the server sends each tool's schema from
`agent/tools.py` unchanged, and `tests/test_mcp_server.py` fails if the two
differ. The two guarantees that run on the loop's side do not: the client's own
model writes the answer, so nothing appends the note or checks the numbers.
The server sends a tool's note as a second block, `Quote this note verbatim in
the answer: …`, and the one model measured did quote it, but nothing makes it.

**It holds no connection between calls.** Each call opens the warehouse
read-only and closes it, so a build can run while a client is connected, and a
call made while the build holds the file answers `error: the warehouse is
locked, most likely by a build; try again when it finishes` in about a
hundredth of a second. A connection held for the client's session would make
every build fail for as long as the client stayed open.

## The metrics

A metric is defined once, in `dbt/models/marts/retail/_retail_metrics.yml`:
net revenue in pounds, euros and dollars, gross revenue, returns, units sold,
orders, customers, average order value, average selling price and return rate,
each with its filter and a description the model reads in the tool's schema.
MetricFlow, which dbt-core already installs, compiles a request to SQL, and
`agent/metrics.py` runs it on the loop's read-only connection
([0015](decisions/0015-metrics-in-the-semantic-layer.md)).

```
$ just metric net_revenue_gbp orders customers --by year
net_revenue_gbp, orders, customers by year, all dates, 2009-12-01 to 2011-12-09
year  net_revenue_gbp  orders  customers
2009       781,541.69   1,820        951
2010     9,570,429.41  20,625      4,205
2011     9,032,384.97  18,916      4,215
all     19,384,356.08  41,361      5,853
customers is a distinct count, and its rows add up to more than "all": one can be counted in several rows.

Partly covered: 2009 (1 Dec to 31 Dec only), 2011 (1 Jan to 9 Dec only). To compare periods, use explain_change, which aligns them.
```

What the tool adds to MetricFlow, which does none of it:

- **The `all` row is a second, ungrouped query**, never the rows added up:
  MetricFlow does not roll distinct counts up, and a customer who bought in two
  years is one customer. The table says a distinct count overcounts only where
  its rows do exceed `all`; orders never do here, because an invoice falls on
  one day and in one country.
- **Money is printed to the penny.** Float sums differ between runs in the ninth
  significant figure, and a model quotes whatever digits it is shown.
- **Only `year`, `quarter`, `month`, `country` and `region` can be grouped by**,
  and a `where` value is checked against the countries and regions that have
  sales before it reaches the SQL. More than 60 rows is refused, with a request
  to narrow it.
- **A period the data covers only in part is named** in a note the loop appends
  verbatim, below.

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
- **A tool's note is appended by the loop, verbatim.** When a comparison
  was cut to the days both years cover, no model tried said what the full
  years would have read, including when told to. `query_metric`'s note works
  the same way: a total for 2011 is a total for 1 January to 9 December, and
  the answer says so whatever the model writes. A note is added unless the
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
one tool once, the right one, with the right arguments, and none had a number
flagged.

| Model | Question | What the answer got wrong |
|---|---|---|
| `granite4.1:8b` (5.3 GB), about a minute | 2010 → 2011, EUR | two subtotals given to one of their bars: the continuing-SKU total, −17.69 pts, as volume's, and the churn total, +€1,954.8k, as new SKUs' |
| `gemma4:26b-a4b-it-q4_K_M` (17 GB), about a minute and a half | 2010 → 2011, EUR | nothing: each subtotal with its own bars under it |
| `granite4.1:8b`, under half a minute | 2008 → 2009 | nothing: the tool's refusal relayed, that the data starts on 2009-12-01 |
| `granite4.1:8b`, under half a minute | 2009 → 2010, GBP | "the comparison covers the full calendar years", above the note saying 1–31 December. A system-prompt line telling it not to describe the period changed nothing on a re-run, and was taken out |
| `granite4.1:8b`, under a minute | net revenue by quarter in 2011 (`query_metric`) | nothing: the four quarters and the `all` row as the year's total, with the Q4 note appended |
| `granite4.1:8b`, under half a minute | which region bought the most in 2010, and its average order value | nothing: it read "the most" as orders and quoted both |
| `granite4.1:8b`, under a minute | can `unit_price` be added up (`describe_model`) | said the column's *description* marks it `non_additive`, where that is its label; the advice, sum `line_amount_gbp`, is right |
| `gemma4:26b-a4b-it-q4_K_M`, about a minute | net revenue by quarter in 2011 | nothing, though the figures lost their £ |
| `gemma4:26b-a4b-it-q4_K_M`, about a minute | which region bought the most in 2010 | named the region with the most revenue without quoting the revenue |
| `gemma4:26b-a4b-it-q4_K_M`, under a minute | can `unit_price` be added up | nothing: no, because it is non-additive |

The two failures are the two kinds the checks cannot see, and both are the
smaller model's. On the metric and table questions neither model wrote a wrong
figure: with every total already on its own row, there was nothing to add up. To compare models on a question, run it twice with
`AGENT_MODEL` changed; the scripted-model tests are what hold the loop itself.

## Adding a tool

A `Tool` in `agent/tools.py` is its OpenAI `tools` schema and a
`run(args) -> ToolResult`. `ToolResult.text` is what the model reads, and
`ToolResult.note`, when set, is what every answer carries verbatim. Add the tool
to `warehouse_tools(con, catalog)`, which both the loop and the MCP server
offer, and write its text the way `render` writes
the bridge: every figure a reader could want already computed and labelled, so
the model has nothing to add up. A `ValueError` or `TypeError` from `run` goes
back to the model as `error: …`, for it to relay or to retry with other
arguments.

**Adding a metric is yml, not Python.** A metric added to
`_retail_metrics.yml` is in `query_metric`'s schema after the next
`just dbt-parse`, because the schema's choices are read from the manifest. It
has to follow the rules `tests/test_semantic_layer.py` holds, which are in the
`contracts-and-data-quality` skill: a measure is a bare column, and a sum reads
only an `additive` one.

### From an MCP client

Claude Code as the client (`claude -p`, with `sonnet` as the model), started
outside the repo, so none of its instructions applied, with its built-in tools
off and this server alone loaded. Both notes were quoted verbatim, and no answer
held a number missing from the tool output (checked with the loop's check,
`agent.loop.unverified`).

| Question | Calls | What the answer got wrong |
|---|---|---|
| net revenue by quarter in 2011 | `query_metric`, by quarter, 2011 | nothing: the four quarters and the `all` row, and the Q4 note |
| which region bought the most in 2010, and its average order value | `query_metric` by region, then again for the average order value filtered to that region | nothing |
| can `unit_price` be added up | `describe_model` | nothing: no, because it is non-additive, with the metric to use instead |
| why did revenue fall from 2010 to 2011 | `explain_change`, in EUR by default | nothing: the aligned −3.19% with the note, and each subtotal with its own bars |
