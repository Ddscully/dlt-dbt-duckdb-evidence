"""`AGENTS.md` is the instructions file; the other agents' entry points must reach it.

Every agent reads its own path, and each way of pointing one at the shared copy
fails without an error:

- Claude Code reads `CLAUDE.md`, never `AGENTS.md`, so the shared file reaches it
  only through an `@AGENTS.md` import. Delete that line and Claude starts every
  session with the plugin section and nothing else.
- Claude Code reads skills only from `.claude/skills/`, and Codex only from
  `.agents/skills/`. One is a symlink to the other; a real directory in its
  place is a second copy of eighteen skills, drifting from the first.
- Codex reads `project_doc_max_bytes` of `AGENTS.md` — 32 KiB by default — and
  cuts the rest, saying so only in a trace log. The budget is one number for
  every `AGENTS.md` from the repo root down to the working directory, root
  first, and it is user configuration the repo cannot set.
- A skill is chosen by its frontmatter `description`, which is YAML. In an
  unquoted scalar ` #` starts a comment, so everything after it is dropped and
  the agent is shown the truncated text — the skill still loads, and a session
  whose task matched only the lost half never picks it.

Sources checked 2026-09-15: code.claude.com/docs/en/memory (the import and the
symlink), and openai/codex `codex-rs/core/src/agents_md.rs` (`data.truncate`
under a `tracing::warn!`) with `DEFAULT_PROJECT_DOC_MAX_BYTES = 32 * 1024` in
`codex-rs/config/src/config_toml.rs`.
"""

from __future__ import annotations

import re
import subprocess

import pytest
import yaml

from modern_data_stack.paths import project_root

ROOT = project_root()
AGENTS_MD = ROOT / "AGENTS.md"
CLAUDE_MD = ROOT / "CLAUDE.md"
SKILLS = sorted((ROOT / ".agents" / "skills").glob("*/SKILL.md"))

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


def test_agents_md_fits_the_budget_codex_reads_by_default():
    """Every tracked `AGENTS.md`, together, inside Codex's default 32 KiB.

    Codex concatenates each `AGENTS.md` from the project root down to the
    working directory against one `remaining` budget and truncates the rest, so
    a nested file counts against the root's allowance. Summing every tracked one
    is the bound for the deepest working directory, and it is exact while the
    root file is the only one. The budget cannot be raised from the repo, which
    is why the file is held under it rather than a notice asking each reader to
    raise it, which would reach only the users who read it.
    """
    files = subprocess.run(
        ["git", "ls-files", "AGENTS.md", "*/AGENTS.md"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    total = sum((ROOT / f).stat().st_size for f in files)
    assert files, "no tracked AGENTS.md — every agent but Claude Code has lost its instructions"
    assert total <= CODEX_DEFAULT_BUDGET, (
        f"{', '.join(files)} total {total:,} bytes, past Codex's default "
        f"{CODEX_DEFAULT_BUDGET:,}: Codex silently drops the last "
        f"{total - CODEX_DEFAULT_BUDGET:,}. A section every session does not need "
        f"belongs in the skill for its area (AGENTS.md, *Agent skills*)."
    )


def test_there_are_skills_to_check():
    """The parametrised test below passes by not running if the glob stops matching."""
    assert SKILLS, "no .agents/skills/*/SKILL.md found — the skills directory moved"


@pytest.mark.parametrize("skill", SKILLS, ids=[s.parent.name for s in SKILLS])
def test_skill_frontmatter_says_what_its_source_says(skill):
    """`name` is the directory, and `description` survives YAML intact.

    Found when `linting-and-type-checking` was written with "--fix deleting # noqa
    prose" in its description: Claude Code's skill list showed it ending at
    "deleting", and nothing failed. The rule is YAML's rather than a parser's — a
    comment needs whitespace before its `#` — so the check is that an unquoted
    description parses to exactly the characters written. A quoted or block scalar
    may hold a `#` legitimately, and is only required to parse to a non-empty
    string.

    `name` must match the directory: the Agent Skills specification requires it
    (agentskills.io/specification, checked 2026-09-15).
    """
    lines = skill.read_text().splitlines()
    assert lines and lines[0] == "---", f"{skill.parent.name}: SKILL.md does not open with ---"
    end = lines.index("---", 1)
    front = yaml.safe_load("\n".join(lines[1:end]))
    assert front["name"] == skill.parent.name, (
        f"{skill.parent.name}: frontmatter name is {front['name']!r}"
    )
    raw = next(line for line in lines[1:end] if line.startswith("description:"))
    written = raw[len("description:") :].strip()
    parsed = front["description"]
    assert isinstance(parsed, str) and parsed.strip(), (
        f"{skill.parent.name}: description is empty or not text"
    )
    if not written.startswith(('"', "'", "|", ">")):
        assert parsed == written, (
            f"{skill.parent.name}: the description as written is {len(written)} "
            f"characters and agents are shown {len(parsed)} — YAML dropped "
            f"{written[len(parsed) :]!r}. An unquoted ' #' starts a comment; "
            f"reword it or quote the description."
        )
