# Claude Code session logs

A local working record of the Claude Code sessions used to build this project.
Export transcripts here (Markdown) so the reasoning behind a change is at hand
while the work is still live.

## Naming

```
YYYY-MM-DD-short-topic.md
```

e.g. `2026-07-17-scaffold-and-wdi.md`.

## How to export

From the Claude Code CLI, the current session transcript can be saved with:

```
/export
```

then move/rename the resulting Markdown file into this folder, or paste the
transcript into a new `YYYY-MM-DD-topic.md` file here.

## This directory is gitignored

`docs/sessions/` is in `.gitignore`. Transcripts are long, they duplicate
whatever the commits and `CLAUDE.md` already say, and leaving them untracked in
a tracked directory meant a `git add -A` could sweep in scratch notes that were
never meant to ship. Nothing here is committed unless you `git add -f` it.

Three files predate the rule and are still tracked — this README and the two
`2026-07-17-*` logs. Durable lessons belong in `../../CLAUDE.md`, not here: the
gotchas file is the part of this history that's meant to survive.
