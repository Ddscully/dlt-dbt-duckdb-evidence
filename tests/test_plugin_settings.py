"""`.claude/settings.json` — the plugin declarations that Claude Code reads.

One invariant here is load-bearing and invisible: two enabled plugins both claim
`.py` for a language server, and which one wins is decided by their order in
`enabledPlugins`.
"""

from __future__ import annotations

import json

from modern_data_stack.paths import project_root

SETTINGS = project_root() / ".claude" / "settings.json"

TY_LSP = "ty-lsp@modern-data-stack"
ASTRAL = "astral@astral-sh"

# Every plugin this repo offers a contributor who trusts it. An exact set rather
# than a membership check: a plugin added here is offered to everyone who opens
# the repo, so it should be a deliberate edit in a diff, not something that
# accumulates. Removing one should be equally visible — that is how the astral
# entry went missing once, silently, as a side effect of an unrelated CLI
# command that rewrote this file.
DECLARED = {
    "dbt@dbt-agent-marketplace",
    "dagster-expert@dagster",
    "polars@polars",
    "duckdb-skills@duckdb-skills",
    TY_LSP,
    ASTRAL,
    "skill-creator@claude-plugins-official",
}


def enabled() -> list[str]:
    return list(json.loads(SETTINGS.read_text())["enabledPlugins"])


def test_the_declared_plugins_are_exactly_what_is_enabled():
    assert set(enabled()) == DECLARED


def test_ty_lsp_is_declared_before_astral():
    """Whichever `.py` language server registers first wins, and the loser is a
    `[WARN]` in the debug log that nothing surfaces.

    Astral's plugin runs `uvx ty@latest server` — the newest published ty on
    every launch. `just typecheck` runs `uv run ty`, the version in `uv.lock`.
    ty is 0.0.x and its diagnostics move between patch releases, so letting
    Astral's win means the editor and the recipe can disagree, with the tree at
    zero and no way to reproduce whatever the editor is showing. That is the
    sqlfluff 3.3.0/4.2.2 split in a new outfit.

    Ordering in a JSON object is not something anyone expects to matter, and
    `astral` sorts before `ty-lsp` — so alphabetising this file, which is the
    obvious tidy-up, silently hands `.py` to the unpinned server. Hence a test
    rather than a comment (JSON has nowhere to put one).

    Verify by hand with: `claude --debug -p ok` then
    `grep 'already handled by' ~/.claude/debug/latest`.
    """
    keys = enabled()
    assert keys.index(TY_LSP) < keys.index(ASTRAL), (
        f"{TY_LSP} must be declared before {ASTRAL} in {SETTINGS}, or the "
        f"unpinned `uvx ty@latest` server takes over .py — got {keys}"
    )
