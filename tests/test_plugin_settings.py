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
# the repo, so it should be a deliberate edit in a diff, not something that
# accumulates. Removing one should be equally visible — that is how the astral
# entry went missing once, silently, as a side effect of an unrelated CLI
# command that rewrote this file.
DECLARED = {
    "dbt@dbt-agent-marketplace",
    TY_LSP,
    "skill-creator@claude-plugins-official",
}


def declarations() -> dict[str, bool]:
    return json.loads(SETTINGS.read_text())["enabledPlugins"]


def enabled() -> list[str]:
    """The plugins actually switched on — **the values, not the keys.**

    Reading `list(...)` here is what this did until 2026-09-02, and it made a
    disabled plugin indistinguishable from an enabled one: the working tree
    carried `dagster-expert@dagster: false` and `polars@polars: false` while
    this test passed and `CLAUDE.md`'s plugin table still described both as
    enabled. A key-scan cannot see a value, which is the same class of blindness
    as every other list-that-fails-green in this repo.
    """
    return [name for name, on in declarations().items() if on]


def test_the_declared_plugins_are_exactly_what_is_enabled():
    assert set(enabled()) == DECLARED


def test_no_plugin_is_declared_and_switched_off():
    """`false` is not a state this file may rest in — delete the entry instead.

    A disabled entry is the worst of both: it reads as a declaration in a diff
    and in `CLAUDE.md`'s table, contributes nothing at runtime, and (before the
    test above learned to read values) was invisible to the guard. The repo has
    two ways to retire a plugin and neither is `false` — remove the entry and
    keep the marketplace registered, which is one line from re-enabling it (the
    `astral` treatment, and now `dagster-expert` and `polars`), or remove the
    marketplace too when the decision is final (the `duckdb-skills` treatment).

    The user-level `~/.claude/settings.json` is the one place `false` earns its
    keep, because there it *overrides* a project declaration; see the
    `agent-plugin-hygiene` note for what that file carries and why nothing in
    the repo records it.
    """
    off = sorted(name for name, on in declarations().items() if not on)
    assert not off, (
        f"declared but switched off: {off} — remove the entry rather than "
        f"setting it false, so the diff and CLAUDE.md's table agree with runtime"
    )


def test_the_two_measured_removals_stay_removed():
    """`dagster-expert@dagster` and `polars@polars`, retired 2026-09-02.

    Measured the same way `astral` and `duckdb-skills` were: parse every
    transcript under `~/.claude/projects/-home-dman-Apps-data-engineering/` and
    count `Skill` tool calls. **Zero invocations each across 211 transcripts
    spanning 2026-08-09 to 2026-09-02** — and the window is not the reason,
    because 9 commits touched `orchestration/` and 9 touched `transform/` inside
    it. The work happened; the skills were not reached for.

    Both are skill-only (one `SKILL.md` each, no LSP, MCP, command, hook or
    agent), which is what makes a zero count admissible evidence at all — the
    caveat that protects `ty-lsp`, whose surface never appears as a `Skill`
    call. Together they cost roughly 340 tokens of always-loaded description.

    `dagster-expert` is the clearer case: its own description sells the **`dg`
    CLI**, which this project deliberately does not install (`dg` appears in
    neither `pyproject.toml` nor `uv.lock`), and `dagster-graph-and-jobs` covers
    Dagster *in this repo* — exactly the shape of the `querying-the-warehouse`
    argument that retired `duckdb-skills`. `polars` is the weaker one and is
    recorded as such: **nothing replaces it**, so it is the first to reconsider
    if the two Polars transforms ever grow into a layer.

    Both marketplaces stay registered, for the reason `astral-sh` does:
    removing a github marketplace *uninstalls* its plugins and a re-register
    needs a clone, which a non-interactive session will not do.
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


def test_claude_mds_plugin_table_lists_exactly_what_is_enabled():
    """The table is a hand-maintained copy of `enabledPlugins`, so it is held to it.

    Every other duplicated list in this repo is asserted against its authority;
    this one was not, and it drifted twice before anyone looked. `CLAUDE.md`
    described `dagster-expert` and `polars` as enabled while the working tree had
    them switched off, and `adding-a-data-source` was still naming
    `duckdb-skills` a week after that plugin was removed.

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
