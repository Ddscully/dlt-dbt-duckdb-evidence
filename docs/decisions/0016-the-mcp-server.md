# 0016. The analyst's tools are served over MCP on stdio, from the loop's own schemas, with a connection per call

Status: accepted 2026-09-28 (#116)

## Context

The finance agent's tools (`explain_change`, `describe_model`, `query_metric`)
were reachable only through `just ask`, the loop for a local model
(`docs/FINANCE_AGENT.md`). An MCP server makes them callable from any MCP client
— Claude Code, Claude Desktop, an IDE — whose own model writes the answer.

Measured before building, against `mcp` 2.2.0, whose server API is not 1.x's:

- **The low-level server takes each tool's JSON Schema as given**, so the
  loop's schemas pass through unchanged; the three tools list at 3.6 KB.
- **The SDK does not check arguments against the schema.** An unknown metric
  and `"group_by": 5` both reached the tool, and the second came back as
  `'int' object is not iterable`.
- **The lock is across processes.** While the server held a read-only
  connection, a writer in another process failed with `Could not set lock`;
  once it closed, the writer opened. While a writer held the file, a read-only
  connect failed in about 10 ms. Left uncaught, that reached an MCP client as
  a bare `Internal server error`, the reason lost.
- **A read-only connection per call costs 0.15–0.4 s**, MetricFlow's compile
  included; the manifests are read once, at start.
- **`mcp` adds 12 packages to the lock**, `cryptography` among them, and moves
  no existing pin.

## Decision

- **stdio only.** The client launches the server, and nothing listens on a
  port.
- **The low-level `Server`, fed the loop's schemas**, from one tool list in
  `agent/tools.py` that both use; a test fails if what the server lists differs
  from what the loop sends.
- **A read-only connection per call, never one held**, and a connect refused
  by the lock is an error result naming the build, for the client's model to
  relay.
- **A tool's note is a second content block** that asks to be quoted verbatim,
  since the loop that appends it is not in the path.
- **`mcp` is in the `dev` dependency group.**
- **No `.mcp.json` is committed**; `docs/FINANCE_AGENT.md` gives the
  `claude mcp add` command and a Claude Desktop entry instead.

## Rejected

- **Streamable HTTP.** A listening port needs authentication and a decision
  about where it runs, and every client here launches local servers.
- **The SDK's decorator server (`MCPServer`, formerly FastMCP).** It infers each
  tool's schema from Python type hints: a second schema per tool, free to
  drift from the one the loop sends, which is the drift `tests/test_agent_loop.py`
  exists to catch.
- **One connection held for the session.** A server lives as long as its
  client, so every build would fail for as long as someone had a client open.
- **`mcp` as a runtime dependency.** Twelve packages in every install and in
  the image, for a server that runs on the client's machine and never in the
  container.
- **A dependency group of its own.** Every `just test` and CI invocation would
  need the flag, or the server's tests would skip without a sound.
- **A committed `.mcp.json`.** It would offer the server to every Claude Code
  session opened in the repo, including the coding sessions the skills are
  written for; agent context is kept by measured use here
  ([0004](0004-agent-plugins-kept-by-measured-use.md)).
- **Making the loop an MCP client of its own server.** It calls the same tools
  in process; a subprocess and a protocol between them would add nothing.

## Consequences

Over MCP the figures and the refusals are the tools', but the prose is the
client's, and two of the loop's guarantees do not travel: nothing appends the
note, and nothing checks the answer's numbers. Claude Code with Sonnet quoted
both notes it was sent, and wrote no number the tools had not printed, on four
questions; that is a measurement of one client, not a property of the server.
MCP resources and prompts, and an HTTP transport for a hosted deployment, are
not built.
