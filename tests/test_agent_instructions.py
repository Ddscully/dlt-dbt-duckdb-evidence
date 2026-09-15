"""`AGENTS.md` is the instructions file; the other agents' entry points must reach it.

Every agent reads its own path, and each way of pointing one at the shared copy
fails without an error:

- Claude Code reads `CLAUDE.md`, never `AGENTS.md`, so the shared file reaches it
  only through an `@AGENTS.md` import. Delete that line and Claude starts every
  session with the plugin section and nothing else.
- Claude Code reads skills only from `.claude/skills/`, and Codex only from
  `.agents/skills/`. One is a symlink to the other; a real directory in its
  place is a second copy of fifteen skills, drifting from the first.
- Codex reads `project_doc_max_bytes` of `AGENTS.md` — 32 KiB by default — and
  cuts the rest, saying so only in a trace log.

Sources checked 2026-09-15: code.claude.com/docs/en/memory (the import and the
symlink), and openai/codex `codex-rs/core/src/agents_md.rs` (`data.truncate`
under a `tracing::warn!`) with `DEFAULT_PROJECT_DOC_MAX_BYTES = 32 * 1024` in
`codex-rs/config/src/config_toml.rs`.
"""

from __future__ import annotations

import re
import subprocess

from modern_data_stack.paths import project_root

ROOT = project_root()
AGENTS_MD = ROOT / "AGENTS.md"
CLAUDE_MD = ROOT / "CLAUDE.md"

CODEX_DEFAULT_BUDGET = 32 * 1024

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_FENCE = re.compile(r"^```.*?^```", re.DOTALL | re.MULTILINE)


def test_claude_md_imports_agents_md():
    """A bare `@AGENTS.md` line, outside comments and code.

    Claude Code skips imports inside code spans and fenced blocks, and strips
    block-level HTML comments before reading the file, so an import in any of
    those is no import. Backticked `@AGENTS.md` in prose is a mention, and the
    whole-line match leaves it out.
    """
    text = _FENCE.sub("", _HTML_COMMENT.sub("", CLAUDE_MD.read_text()))
    imports = [line for line in text.splitlines() if line.strip() == "@AGENTS.md"]
    assert imports, (
        "CLAUDE.md no longer imports AGENTS.md — Claude Code does not read "
        "AGENTS.md itself, so every shared instruction is gone from its sessions"
    )


def test_claude_skills_is_a_symlink_to_the_shared_skills():
    """Checked in the index, where the mode is recorded, and on disk.

    The index is what a clone gets. A real directory shows up there as files
    under `.claude/skills/`, and a symlink as one `120000` entry.
    """
    entries = subprocess.run(
        ["git", "ls-files", "--stage", "--", ".claude/skills"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert len(entries) == 1 and entries[0].startswith("120000 "), (
        f".claude/skills is not tracked as a single symlink ({len(entries)} "
        f"entries) — the skills live in .agents/skills, and a copy here drifts"
    )
    assert (ROOT / ".claude" / "skills").resolve() == ROOT / ".agents" / "skills"


def test_the_codex_budget_notice_is_one_codex_reads_and_is_enough():
    """The notice has to sit inside the budget it warns about, and be right.

    A notice past byte 32,768 is cut off with everything else it explains. The
    value it recommends has to cover the file too, or following the advice still
    truncates.

    Once `AGENTS.md` is trimmed under the default budget, delete the notice and
    replace this with `len(AGENTS_MD.read_bytes()) <= CODEX_DEFAULT_BUDGET`.
    """
    data = AGENTS_MD.read_bytes()
    match = re.search(rb"project_doc_max_bytes = (\d+)", data)
    assert match, "AGENTS.md no longer tells Codex users to raise its budget"
    assert match.end() <= CODEX_DEFAULT_BUDGET, (
        f"the project_doc_max_bytes notice ends at byte {match.end()}, past "
        f"Codex's default {CODEX_DEFAULT_BUDGET} — Codex truncates before it"
    )
    assert int(match.group(1)) >= len(data), (
        f"AGENTS.md is {len(data)} bytes but its notice recommends "
        f"project_doc_max_bytes = {int(match.group(1))} — raise the figure"
    )
