<!--
Everything shared with other agents is in AGENTS.md, pulled in by the import
below. This file holds only what Claude Code alone reads: its plugin and
marketplace declarations. Claude Code strips block-level HTML comments before
the file reaches the model, so this note costs no context.
-->

@AGENTS.md

## Claude Code plugins

The shared instructions are [`AGENTS.md`](AGENTS.md), which every agent reads
and the `@AGENTS.md` line imports; this section is what only Claude Code does.
Claude Code loads the import as a file of its own, after this one, not spliced
in at that line — the model sees the line itself, then this section, then
`AGENTS.md` (observed 2026-09-15 on Claude Code 2.1.272) — so nothing here may
say the shared text is "above".

Vendor skills for each layer are declared in [`.claude/settings.json`](.claude/settings.json),
so Claude Code offers to install them when you trust this repo. They carry the
tool-level knowledge; `AGENTS.md` and the project skills carry the repo-level
knowledge.

| Plugin | Covers |
|--------|--------|
| `dbt@dbt-agent-marketplace` | [dbt Labs' skills](https://github.com/dbt-labs/dbt-agent-skills) — models, tests, docs, debugging |
| `skill-creator@claude-plugins-official` | authoring and evaluating the project skills in `.agents/skills/` — the one entry about the repo's own tooling rather than a layer of the stack |

Not enabled, but worth knowing about: `dbt-migration@dbt-agent-marketplace`
(one-off dbt Core → Fusion work), `dignified-python@dagster`, and dltHub's
[AI Workbench](https://github.com/dlt-hub/dlthub-ai-workbench)
(`/plugin marketplace add dlt-hub/dlthub-ai-workbench`) — the workbench assumes
its own scaffolding, so prefer the `adding-a-data-source` skill for the pipeline
that already exists here.

**A plugin keeps its place by being used, and use is measured** — by counting
`Skill` invocations across the session transcripts, and checking in `git log`
that the plugin's layer was actually being worked on in the window. Four were
retired on a count of zero: `duckdb-skills` and `astral` (187 transcripts, to
2026-08-27), `dagster-expert` and `polars` (211 transcripts, to 2026-09-02). A
zero count is only evidence for a skill-only plugin; an LSP's use never appears as
a `Skill` call.

- **`dagster-expert` sells the `dg` CLI**, which this project deliberately does
  not install, and `dagster-graph-and-jobs` covers Dagster *in this repo*.
  `duckdb-skills` was the same shape — ad-hoc file querying, S3, spatial joins —
  against `querying-the-warehouse`. **`polars` is the weak call**: nothing
  replaces it, so it is the first to reconsider if `transform/` grows.
- **`astral` could not be reached at all.** It and `ty-lsp` both declare a ty
  language server for `.py`/`.pyi`, the first loaded wins, and `ty-lsp` has to:
  Astral's runs `uvx ty@latest`, the newest ty on every launch, against a
  `just typecheck` that runs `uv.lock`'s — the editor would show findings the
  recipe cannot reproduce. With its server shadowed and its skills unused,
  `astral` was two `[WARN]` lines in the debug log.
  `tests/test_plugin_settings.py` asserts it stays off, with the ordering rule in
  the failure message for whoever re-enables it. Check by hand with
  `claude --debug -p ok` then `grep 'already handled by' ~/.claude/debug/latest`
  — no output is the passing state.
- **Retire a plugin by deleting its entry, never with `false`.** A `false` entry
  reads as a declaration and does nothing; the plugin test fails on one.
- **A retired plugin's `github` marketplace stays registered** (`astral-sh`,
  `dagster`, `polars`) unless the decision is final (`duckdb-skills`). Removing a
  github marketplace *uninstalls* its plugins, and the project declaration does
  not bring them back, because re-registering needs a clone that a
  non-interactive session will not make; the cache survives, so the only symptom
  is plugins quietly missing. A user-level entry for a github marketplace is not a
  duplicate of the project one — leave it.

`.claude/marketplace/` is a repo-local marketplace holding `ty-lsp`, which runs
the dev group's ty as a language server — there is no published ty plugin, and an
LSP server is a ten-line `.lsp.json`. Its command is `uv run ty server`, so it
runs `uv.lock`'s ty and must be launched from the project root. A `directory`
marketplace resolves from a **relative** path (`./.claude/marketplace`) and is
read live from the repo, so editing it needs no reinstall, and removing it from
user settings is safe — the project declaration re-registers it.
`claude plugin marketplace add` writes an *absolute* path into user settings, so
declare it in `.claude/settings.json` by hand.

**`.claude/skills` is a symlink to `.agents/skills`**, and Claude Code follows
it. Replacing it with a real directory gives two copies of every skill that
drift; `tests/test_agent_instructions.py` fails on that, and on this file
losing its `@AGENTS.md` line, which would leave Claude Code this section and
nothing else, with no error.
