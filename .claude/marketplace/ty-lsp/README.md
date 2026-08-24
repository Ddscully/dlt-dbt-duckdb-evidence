# ty-lsp

Runs [ty](https://docs.astral.sh/ty/) as the Python language server for this
project, so Claude Code (and any editor pointed at the same config) gets real
type diagnostics and go-to-definition instead of grep.

## Why this is a repo-local plugin

There is no published ty plugin — the official marketplace has 286 of them and
none for anything Astral ships. `pyright-lsp@claude-plugins-official` exists and
was the obvious alternative; it was measured against this tree and rejected:

| | ty 0.0.74 | pyright |
|---|---|---|
| diagnostics here | 38 | 45 errors + 2 warnings |
| wall time | 0.32 s | 4.83 s |
| found `.venv` unaided | yes | no — 54 phantom missing-imports until pointed at it |
| install | `uv add --group dev` → pinned in `uv.lock` | `npm install -g pyright`, unpinned and global |

The install row is the one that decided it. An unpinned global Node binary that
no lockfile in this repo can see is the same shape as the sqlfluff 3.3.0/4.2.2
split that once made `just lint` pass and the commit hook fail — see the dev
group in `pyproject.toml`.

## Why the command is `uv run ty`, not `ty`

Same reason. A bare `ty` on `PATH` would be a second copy free to drift from the
one in `uv.lock`; `uv run` resolves the locked version. The cost is that the
server has to be launched with the project root as its working directory, which
is what `uv run` walks up from to find `pyproject.toml`.

## It has to outrank `astral@astral-sh`

Astral publishes its own plugin (uv/ruff/ty skills, enabled here) and it ships a
ty language server too — `uvx ty@latest server`, the newest published ty on every
launch. This one is `uv run ty server`, the version in `uv.lock`.

Both claim `.py`; the first loaded wins and the loser is a `[WARN]` nobody sees.
The order is the order of `enabledPlugins` in `.claude/settings.json`, where
`ty-lsp` sits above `astral` — and `astral` sorts first alphabetically, so
tidying that block would quietly swap them. `tests/test_plugin_settings.py`
fails if it does.

## Install

Declared in `.claude/settings.json`, so Claude Code offers it when you trust the
repo. By hand:

```bash
claude plugin marketplace add ./.claude/marketplace
claude plugin install ty-lsp@modern-data-stack
```

`just typecheck` is the same checker without the editor.
