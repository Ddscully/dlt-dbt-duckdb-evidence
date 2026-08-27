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
    TY_LSP,
    "skill-creator@claude-plugins-official",
}


def enabled() -> list[str]:
    return list(json.loads(SETTINGS.read_text())["enabledPlugins"])


def test_the_declared_plugins_are_exactly_what_is_enabled():
    assert set(enabled()) == DECLARED


def test_astral_is_not_enabled_so_one_server_claims_py():
    """Only one plugin may hold `.py`, and this repo's answer is `ty-lsp`.

    Both plugins declare a `ty` language server for `.py` and `.pyi`. Whichever
    registers first wins and the loser is two `[WARN]` lines in
    `~/.claude/debug/latest` that nothing surfaces:

        LSP: extension .py already handled by "plugin:ty-lsp:python";
        "plugin:astral:ty" will not be used for .py files

    That ordering used to be the invariant here — `ty-lsp` before `astral` in
    `enabledPlugins`, because ordering in a JSON object is not something anyone
    expects to matter and `astral` sorts first, so alphabetising the file
    silently handed `.py` to the unpinned server. The ordering *worked*. What
    measurement then showed is that winning it left `astral` contributing
    nothing at all: its LSP declares `.py`/`.pyi` and no other extension, so it
    was dead by construction, and its three skills (ruff, ty, uv) had **zero**
    invocations across 187 transcripts spanning 2026-07-29 to 2026-08-27. A
    plugin whose every surface is unreachable is not a plugin, it is two
    warnings.

    Which server would win is why `ty-lsp` is the one kept. Astral's runs
    `uvx ty@latest server` — the newest published ty on every launch — against a
    `just typecheck` that runs the version in `uv.lock`. ty is 0.0.x and its
    diagnostics move between patch releases, so the editor would show findings
    the recipe cannot reproduce. That is the sqlfluff 3.3.0/4.2.2 split in a new
    outfit.

    The `astral-sh` marketplace stays registered in `.claude/settings.json` on
    purpose: removing a github marketplace *uninstalls* its plugins and a
    re-register needs a clone, which a non-interactive session will not do. So
    re-enabling is a one-line edit — and if you make it, put `astral` **after**
    `ty-lsp` and expect its LSP to stay dead.

    Verify by hand with: `claude --debug -p ok` then
    `grep 'already handled by' ~/.claude/debug/latest` — no output is the
    passing state now.
    """
    assert ASTRAL not in enabled(), (
        f"{ASTRAL} is enabled again: it and {TY_LSP} both claim .py, so one of "
        f"them is dead and Claude Code warns about it in the debug log. If this "
        f"is deliberate, declare it after {TY_LSP} and update this test"
    )
