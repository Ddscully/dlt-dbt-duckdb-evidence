# 0004. A vendor agent plugin stays enabled only while it is measurably used

Status: accepted 2026-09-02 (#27, #32)

## Context

`.claude/settings.json` declares vendor plugins so Claude Code offers them to
anyone who trusts the repo. Each one loads its skill descriptions into every
session, whether or not the session touches that plugin's layer. Use is
measurable: count `Skill` invocations across the session transcripts, and check
in `git log` that the plugin's layer was being worked on in that window. A zero
count is evidence only for a skill-only plugin; an LSP's use never appears as a
`Skill` call.

## Decision

Four plugins were retired on a count of zero:

- `duckdb-skills` and `astral`: 187 transcripts, to 2026-08-27.
- `dagster-expert` and `polars`: 211 transcripts, to 2026-09-02, a window with
  commits to `orchestration/` in it.

`dbt@dbt-agent-marketplace` and `skill-creator` stay.

## Rejected

- **Keeping `dagster-expert`.** It is written around the `dg` CLI, which this
  project deliberately does not install, so it argued for a different project;
  `dagster-graph-and-jobs` covers Dagster *in this repo*.
- **Keeping `duckdb-skills`.** The same shape: ad-hoc file querying, S3 and
  spatial joins, against `querying-the-warehouse`.
- **Keeping `astral`.** It could not be reached at all. It and `ty-lsp` both
  declare a ty language server for `.py`/`.pyi`, the first loaded wins, and
  `ty-lsp` has to: Astral's runs `uvx ty@latest`, the newest ty on every launch,
  against a `just typecheck` that runs `uv.lock`'s, so the editor would show
  findings the recipe cannot reproduce. With its server shadowed and its skills
  unused, it was two `[WARN]` lines in the debug log.

## Consequences

- **`polars` is the weak call**: nothing replaces it, so it is the first to
  reconsider if `transform/` grows.
- `tests/test_plugin_settings.py` asserts `astral`, `dagster-expert` and
  `polars` stay off. Whoever re-enables `astral` must load it after `ty-lsp`;
  check by hand with `claude --debug -p ok` and then
  `grep 'already handled by' ~/.claude/debug/latest`, where no output passes.
- The tool-level knowledge for Dagster, Polars and DuckDB is the project skills
  and `AGENTS.md`, not a vendor skill.
