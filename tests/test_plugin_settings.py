"""`.claude/settings.json` — the plugin declarations that Claude Code reads.

One invariant here is load-bearing and invisible: two enabled plugins both claim
`.py` for a language server, and which one wins is decided by their order in
`enabledPlugins`.
"""

from __future__ import annotations

import json
import re

from modern_data_stack.paths import project_root

SETTINGS = project_root() / ".claude" / "settings.json"
CLAUDE_MD = project_root() / "CLAUDE.md"

TY_LSP = "ty-lsp@modern-data-stack"
ASTRAL = "astral@astral-sh"

# Every plugin this repo offers a contributor who trusts it. An exact set rather
# than a membership check: a plugin added here is offered to everyone who opens
# the repo, so adding one should be a deliberate edit in a diff — and removing
# one equally visible, since a CLI command that rewrites this file can drop an
# entry silently.
DECLARED = {
    "dbt@dbt-agent-marketplace",
    TY_LSP,
    "skill-creator@claude-plugins-official",
}


def declarations() -> dict[str, bool]:
    return json.loads(SETTINGS.read_text())["enabledPlugins"]


def enabled() -> list[str]:
    """The plugins actually switched on — **the values, not the keys.**

    `list(...)` over the dict would make a disabled plugin indistinguishable
    from an enabled one.
    """
    return [name for name, on in declarations().items() if on]


def test_the_declared_plugins_are_exactly_what_is_enabled():
    assert set(enabled()) == DECLARED


def test_no_plugin_is_declared_and_switched_off():
    """`false` is not a state this file may rest in — delete the entry instead.

    A disabled entry reads as a declaration in a diff and in `CLAUDE.md`'s
    table and contributes nothing at runtime. The repo has two ways to retire a
    plugin and neither is `false`: remove the entry and keep the marketplace
    registered, one line from re-enabling it (`astral`, `dagster-expert`,
    `polars`), or remove the marketplace too when the decision is final
    (`duckdb-skills`).

    The user-level `~/.claude/settings.json` is the one place `false` earns its
    keep, because there it *overrides* a project declaration.
    """
    off = sorted(name for name, on in declarations().items() if not on)
    assert not off, (
        f"declared but switched off: {off} — remove the entry rather than "
        f"setting it false, so the diff and CLAUDE.md's table agree with runtime"
    )


def test_the_two_measured_removals_stay_removed():
    """`dagster-expert@dagster` and `polars@polars`, retired 2026-09-02.

    Retired on a measured zero `Skill` invocations across the window in which
    both layers were being edited; CLAUDE.md's *Agent skills* section has the
    measurement and the reasoning. `polars` is the weaker call: nothing replaces
    it, so it is the first to reconsider if `transform/` grows into a layer.

    Both marketplaces stay registered: removing a github marketplace
    *uninstalls* its plugins, and a re-register needs a clone, which a
    non-interactive session will not do.
    """
    for retired in ("dagster-expert@dagster", "polars@polars"):
        assert retired not in declarations(), (
            f"{retired} is back: it had zero Skill invocations across 211 "
            f"transcripts covering 9 commits of the work it covers. If that has "
            f"changed, say so here and update CLAUDE.md's plugin table"
        )


def test_astral_is_not_enabled_so_one_server_claims_py():
    """Only one plugin may hold `.py`, and this repo's answer is `ty-lsp`.

    Both plugins declare a `ty` language server for `.py` and `.pyi`. Whichever
    registers first wins and the loser is two `[WARN]` lines in
    `~/.claude/debug/latest` that nothing surfaces:

        LSP: extension .py already handled by "plugin:ty-lsp:python";
        "plugin:astral:ty" will not be used for .py files

    With `ty-lsp` first, `astral` has no reachable surface: its LSP declares
    only `.py`/`.pyi`, and its skills were never invoked (CLAUDE.md has the
    measurement). `ty-lsp` is the one kept because Astral's server runs
    `uvx ty@latest`, the newest ty on every launch, against a `just typecheck`
    that runs the version in `uv.lock` — and ty's diagnostics move between patch
    releases, so the editor would show findings the recipe cannot reproduce.

    The `astral-sh` marketplace stays registered, so re-enabling is a one-line
    edit — and if you make it, put `astral` **after** `ty-lsp` in
    `enabledPlugins` (it sorts first, so alphabetising the file hands `.py` to
    the unpinned server) and expect its LSP to stay dead.

    Verify by hand with: `claude --debug -p ok` then
    `grep 'already handled by' ~/.claude/debug/latest` — no output is the
    passing state now.
    """
    assert ASTRAL not in enabled(), (
        f"{ASTRAL} is enabled again: it and {TY_LSP} both claim .py, so one of "
        f"them is dead and Claude Code warns about it in the debug log. If this "
        f"is deliberate, declare it after {TY_LSP} and update this test"
    )


def test_claude_mds_plugin_table_lists_exactly_what_is_enabled():
    """The table is a hand-maintained copy of `enabledPlugins`, so it is held to it.

    Scoped to the table on purpose. Prose may *discuss* a plugin that is not
    enabled — the `dg` bullet in `dagster-graph-and-jobs` does, and the "Not
    enabled, but worth knowing about" paragraph directly under this table exists
    to — so a repo-wide scan for plugin names would have to tell an assertion
    from a mention, which is the ambiguity that keeps `CLAUDE.md`'s backticked
    paths out of the course guard as well. A row in the table is unambiguous.

    **`ty-lsp` is the one enabled plugin with no row, and that is deliberate.**
    It is the repo-local one, and the `.claude/marketplace/` paragraph below the
    table says more about it than a cell could hold — the relative path, the
    `uv run ty server` pinning, and why the marketplace is declared by hand. So
    the table is the *vendor* set, and the assertion says so rather than
    quietly dropping a name: the second half still requires `ty-lsp` to be
    described somewhere in the file, or removing that paragraph would be silent.
    """
    text = CLAUDE_MD.read_text()
    rows = re.findall(r"^\| `([^`]+@[^`]+)` \|", text, re.MULTILINE)
    vendor = DECLARED - {TY_LSP}

    assert rows, "the plugin table in CLAUDE.md has no rows — did its shape change?"
    assert set(rows) == vendor, (
        f"CLAUDE.md's plugin table and .claude/settings.json disagree — "
        f"table only: {sorted(set(rows) - vendor)}, "
        f"settings only: {sorted(vendor - set(rows))}"
    )
    # The bare name, because the prose calls it `ty-lsp` rather than spelling
    # out the marketplace the way a table row would.
    assert TY_LSP.split("@")[0] in text, (
        f"{TY_LSP} is enabled and CLAUDE.md no longer describes it anywhere — "
        f"it has no table row on purpose, so the paragraph is its only mention"
    )
