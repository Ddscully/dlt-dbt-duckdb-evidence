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


def enabled() -> list[str]:
    return list(json.loads(SETTINGS.read_text())["enabledPlugins"])


def test_both_python_plugins_are_enabled():
    assert TY_LSP in enabled()
    assert ASTRAL in enabled()


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
