"""The course material and the project skills, checked against the repo they cite.

`docs/course/` and `.claude/skills/*/SKILL.md` quote file paths, `just` recipes
and module links out of the rest of the tree. None of that is executable, so it
rots the way an exposure does: the module still renders and reads correctly, and
the path it tells a learner to open was renamed six commits ago. A course that
sends someone to a missing file is worse than no course, because the reader
assumes they are the one who is wrong.

The same argument as `tests/test_exposures.py` and `tests/test_report.py`: an
assertion about the *outside* of a system needs a test on the outside of it.

No warehouse and no dbt manifest here — `just test` has neither, so everything
below reads markdown and the justfile as text.
"""

from __future__ import annotations

import pathlib
import re
import shlex
import subprocess
import tempfile

import pytest

from modern_data_stack.paths import project_root

COURSE_DIR = project_root() / "docs" / "course"
INDEX = COURSE_DIR / "README.md"
JUSTFILE = project_root() / "justfile"

# The project skills quote the repo exactly as the course does — several were
# split out of CLAUDE.md so they load only for the task that needs them — so they
# rot the same way and are checked by the same two citation tests below. Not by
# the structural ones: those are about a module's exercises.
#
# Globbed, not listed: from a hand-maintained list a skill could be omitted
# without any error, and nothing would check it.
SKILLS_DIR = project_root() / ".claude" / "skills"

# Top-level directories a module may cite. `data/` is deliberately absent: it is
# gitignored and built, so a path under it is correct even on a fresh clone where
# it does not exist yet.
CITABLE_ROOTS = (
    "dbt",
    "docs",
    "ingest",
    "lake",
    "notebooks",
    "orchestration",
    "reports",
    "scripts",
    "src",
    "tests",
    "transform",
)

# A backticked path, e.g. `dbt/models/marts/country_stats/dim_country_year.sql`.
# Anchored on the citable roots so prose like `country_iso3` and `PASS=402`
# cannot match.
_CITED_PATH = re.compile(
    r"`((?:" + "|".join(CITABLE_ROOTS) + r")/[A-Za-z0-9_./*-]+)`",
)

# `just course-rebuild`. Searched only inside code — a fenced block or an inline
# span — because "just" is also an English word and the prose is full of it
# ("exactly what the publisher just served"). Comments inside a fenced block are
# prose too, and are stripped before the search for the same reason.
_CITED_RECIPE = re.compile(r"\bjust ([a-z][a-z0-9-]*)")
_FENCED = re.compile(r"^```[^\n]*\n(.*?)^```", re.MULTILINE | re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_SHELL_COMMENT = re.compile(r"#[^\n]*")

# A recipe definition in the justfile: name, then optional args which may carry a
# default (`backfill-wdi start end=''`), then the colon. The default matters —
# without it that recipe reads as undefined and every citation of it fails.
_RECIPE_DEF = re.compile(r"^([a-z][a-z0-9-]*)(?: [a-z_]+(?:=[^\s:]*)?)*:", re.MULTILINE)

# A markdown link to a sibling module, e.g. [00 — Setup](./00-setup.md).
_MODULE_LINK = re.compile(r"\]\(\./([0-9]{2}-[a-z0-9-]+\.md)\)")


def modules() -> list:
    """Every numbered module file, in order."""
    return sorted(COURSE_DIR.glob("[0-9][0-9]-*.md"))


def course_files() -> list:
    """The index plus every module — everything a learner reads."""
    return [INDEX, *modules()]


def recipes() -> set[str]:
    """Every recipe name the justfile defines."""
    return set(_RECIPE_DEF.findall(JUSTFILE.read_text()))


def skills() -> list:
    """Every project skill, guarded for the reason the course is."""
    return sorted(SKILLS_DIR.glob("*/SKILL.md"))


def cited_files() -> list:
    """Everything that quotes the repo at a reader: the course, plus the skills."""
    return [*course_files(), *skills()]


def tracked_markdown() -> list[pathlib.Path]:
    """Every markdown file git tracks.

    `git ls-files` rather than a glob, for `tests/test_documented_counts.py`'s
    reason: `docs/sessions/` is gitignored in full, so the transcripts stay
    outside this by construction rather than by an exclude list that could drift.
    """
    out = subprocess.run(
        ["git", "ls-files", "*.md"],
        cwd=project_root(),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return [project_root() / p for p in out]


def _label(doc) -> str:
    """`SKILL.md` alone says nothing; name a skill by its directory."""
    return doc.parent.name if doc.name == "SKILL.md" else doc.name


def _ids(paths) -> list[str]:
    return [_label(p) for p in paths]


def test_the_course_has_an_index_and_at_least_one_module():
    assert INDEX.exists(), "docs/course/README.md is the entry point; it must exist"
    assert modules(), "docs/course/ has an index but no numbered modules"


def ignored(paths: set[str]) -> set[str]:
    """Which of `paths` git ignores.

    A gitignored path is a build artifact: correct to cite, and absent on a fresh
    clone. This is the general form of the rule that keeps `data/` out of
    `CITABLE_ROOTS`, needed because `reports/` mixes source (`pages/`,
    `sources/`) with the output of `just report` (`build/`, `node_modules/`,
    `.evidence/`). Asking git avoids a second list that could drift from the
    .gitignore.

    Every path is asked about twice, with and without a trailing slash, because
    a directory-only pattern (`dbt/target/`) matches only a path the filesystem
    says is a directory. Without the slashed probe, a cited `dbt/target` would be
    exempt on a machine that had built and nowhere else — green locally, red on
    a fresh checkout.
    """
    if not paths:
        return set()
    probes = {form for c in paths for form in (c.rstrip("/"), c.rstrip("/") + "/")}
    done = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        cwd=project_root(),
        input="\n".join(sorted(probes)),
        capture_output=True,
        text=True,
        check=False,  # exit 1 simply means nothing matched
    )
    hits = {line.strip() for line in done.stdout.splitlines() if line.strip()}
    return {c for c in paths if c.rstrip("/") in hits or c.rstrip("/") + "/" in hits}


@pytest.mark.parametrize("doc", cited_files(), ids=_ids(cited_files()))
def test_every_path_a_module_cites_exists(doc):
    """A renamed model must not leave the course pointing at a dead path."""
    # A glob is a description of a set, not a path to open.
    cited = {c for c in _CITED_PATH.findall(doc.read_text()) if "*" not in c}
    missing = sorted(c for c in cited - ignored(cited) if not (project_root() / c).exists())
    assert not missing, f"{_label(doc)} cites paths that no longer exist: {missing}"


def code_spans(text: str) -> list[str]:
    """Every fenced block (comments stripped) and inline code span in a module."""
    return [_SHELL_COMMENT.sub("", block) for block in _FENCED.findall(text)] + (
        _INLINE_CODE.findall(_FENCED.sub("", text))
    )


@pytest.mark.parametrize("doc", cited_files(), ids=_ids(cited_files()))
def test_every_just_recipe_a_module_cites_exists(doc):
    """`just course-rebuild` in a command a learner will paste must be a recipe."""
    defined = recipes()
    missing = sorted(
        {
            cited
            for span in code_spans(doc.read_text())
            for cited in _CITED_RECIPE.findall(span)
            if cited not in defined
        }
    )
    assert not missing, f"{_label(doc)} cites justfile recipes that don't exist: {missing}"


@pytest.mark.parametrize("doc", course_files(), ids=_ids(course_files()))
def test_every_module_link_resolves(doc):
    """The next/previous links and the index table must all land somewhere."""
    missing = sorted(
        {link for link in _MODULE_LINK.findall(doc.read_text()) if not (COURSE_DIR / link).exists()}
    )
    assert not missing, f"{doc.name} links to modules that don't exist: {missing}"


def test_the_index_lists_every_module_that_exists():
    """A module nobody can navigate to may as well not be written."""
    linked = set(_MODULE_LINK.findall(INDEX.read_text()))
    on_disk = {module.name for module in modules()}
    assert on_disk - linked == set(), (
        f"modules exist but the index doesn't link them: {sorted(on_disk - linked)}"
    )


# A cross-file markdown anchor, e.g. [CLAUDE.md](../CLAUDE.md#agent-skills). Only
# links carrying a `#fragment` — a bare link to a file is already covered by the
# citation test above, and a same-file `#anchor` cannot survive a rename anyway.
_ANCHOR_LINK = re.compile(r"\]\((\.{0,2}[/A-Za-z0-9_.-]*\.md)#([A-Za-z0-9_-]+)\)")

# Anything that is not a letter, digit, space, hyphen or underscore. GitHub's
# slug drops it — which is why `## The lakehouse (`lake/lakehouse.py`)` anchors
# as `#the-lakehouse-lakelakehousepy`, backticks, brackets and slashes all gone.
_NOT_IN_SLUG = re.compile(r"[^a-z0-9 \-_]")

# Any heading level, with its text captured. Not `_HEADING`, which this module
# already uses for `^## .*$` in the structural checks below; a second definition
# under that name would silently replace the first.
_ANY_HEADING = re.compile(r"^#+\s+(.+)$", re.MULTILINE)


def slug(heading: str) -> str:
    """GitHub's heading anchor: lowercase, drop punctuation, spaces to hyphens."""
    return _NOT_IN_SLUG.sub("", heading.lower()).replace(" ", "-")


def anchor_links() -> list[tuple[pathlib.Path, pathlib.Path, str]]:
    """Every (source, target file, fragment) in tracked markdown."""
    out = []
    for doc in tracked_markdown():
        for target, fragment in _ANCHOR_LINK.findall(doc.read_text()):
            out.append((doc, (doc.parent / target).resolve(), fragment))
    return out


def headings_in(path: pathlib.Path) -> set[str]:
    """Every anchor a markdown file offers."""
    return {slug(m.group(1).strip()) for m in _ANY_HEADING.finditer(path.read_text())}


def test_every_cross_file_anchor_resolves():
    """A heading that moves leaves the link green in review and dead on click.

    The citation tests above check backticked paths and `just` recipes; a
    `](file#fragment)` link can keep a correct path while its fragment names a
    heading that has gone — which every split of CLAUDE.md into a skill risks.
    """
    dead = []
    for doc, target, fragment in anchor_links():
        rel = doc.relative_to(project_root())
        if not target.exists():
            dead.append(f"{rel} -> {target.name}#{fragment} (no such file)")
        elif fragment not in headings_in(target):
            dead.append(f"{rel} -> {target.name}#{fragment}")
    assert not dead, f"markdown anchors that resolve to no heading: {dead}"


def test_the_anchor_scan_still_finds_anchors():
    """The guard above passes by not looking if `_ANCHOR_LINK` stops matching.

    A scanner whose pattern drifts reports no findings, which is
    indistinguishable from a clean tree.

    Non-emptiness and nothing else: there are only a handful of anchors, and a
    floor on their number would go red on a legitimate deletion.
    """
    assert anchor_links(), (
        "the anchor scan found no `](file.md#fragment)` links at all — "
        "`_ANCHOR_LINK` has stopped matching, so `test_every_cross_file_anchor_"
        "resolves` is now green because it is looking at nothing"
    )


# 🔧 break-and-fix, 🔍 investigate, 💬 design defence. A module marks its
# exercises with these in the section heading.
EXERCISE_MARKERS = ("\U0001f527", "\U0001f50d", "\U0001f4ac")

# Setup carries no exercises by design, so the exercise rules below don't apply
# to it. Exempted by name rather than by "has no markers", which would excuse
# every module that forgot to write any.
SETUP_MODULE = "00-setup.md"

_HEADING = re.compile(r"^## .*$", re.MULTILINE)

REVEAL = "<details>"

# The break-and-fix marker specifically. The index promises that *drills* end
# with a verification query — investigate and design-defence sections have
# nothing to verify, so the promise is narrower than "every exercise".
DRILL_MARKER = "\U0001f527"

VERIFICATION = "**Verification.**"


def missing_verification(heading: str, body: str) -> str | None:
    """The name of what a 🔧 drill section is missing, or None if it is complete.

    `docs/course/README.md` promises a learner: "Every drill ends with a
    **verification query**, because 'it looks right now' is the failure mode the
    course exists to break." An unenforced promise in the index rots the way a
    renamed path does.

    Two checks, because a drill could carry the words and no command:

    - the `**Verification.**` marker is present, and
    - a fenced block follows it, before the reveal.

    "Before the reveal" matters: every reveal is full of fenced SQL, so an
    unbounded search would find a block whatever the drill itself carries.

    Not checked: that the marker sits on a line of its own. `01-grain.md` writes
    "**Verification.** When you think it is fixed:" with the block on the next
    line, which is good prose.

    Called once per section; only 🔧 sections are passed in.
    """
    start = body.find(VERIFICATION)
    if start == -1:
        return "a **Verification.** block"

    reveal = body.find(REVEAL, start)
    drill = body[start:reveal] if reveal != -1 else body[start:]
    if not _FENCED.search(drill):
        return "a runnable command in the **Verification.** block"
    return None


def sections(text: str) -> list[tuple[str, str]]:
    """`[(heading, body), …]` for each `##` section, preamble discarded."""
    starts = [match.start() for match in _HEADING.finditer(text)]
    bounds = [*starts, len(text)]
    return [
        (text[start : text.index("\n", start)], text[start : bounds[i + 1]])
        for i, start in enumerate(starts)
    ]


def is_exercise(heading: str) -> bool:
    return any(mark in heading for mark in EXERCISE_MARKERS)


def missing_sections(text: str, name: str) -> list[str]:
    """What a complete module is missing, or `[]` if it carries everything.

    The tests above keep the course from pointing at things that moved. This is
    the pedagogical contract instead: what a reader is entitled to find in *any*
    module, so a half-written one fails here rather than shipping and
    disappointing someone.

    Two rules beyond the boilerplate:

    - **`00-setup.md` is exempt from the exercise rules by name.** It carries no
      drills by design; exempting "modules with no markers" instead would excuse
      every module that forgot to write any.
    - **Reveals match exercises section by section:** every `##` section whose
      heading is marked must contain at least one `<details>`, and no
      `<details>` may sit outside a marked section. An exercise with no answer
      fails, and so does an answer with no question.

      Not `count(marker) == count(<details>)`: `01-grain.md` has three marked
      headings and five reveals, because its design-defence section asks (a),
      (b) and (c) and answers each.

    Returns human-readable names, so the assertion tells an author what to write
    rather than only that something is wrong.
    """
    missing: list[str] = []

    if "**Objectives.**" not in text:
        missing.append("an **Objectives.** line")
    if "[Course index](./README.md)" not in text:
        missing.append("a nav link back to the course index")

    found = sections(text)
    if not found:
        missing.append("any `##` sections")
        return missing

    exercises = [(heading, body) for heading, body in found if is_exercise(heading)]

    if name != SETUP_MODULE:
        if not exercises:
            missing.append(f"at least one exercise section marked {' '.join(EXERCISE_MARKERS)}")
        if "## What to carry forward" not in text:
            missing.append("a `## What to carry forward` summary")

    for heading, body in exercises:
        if REVEAL not in body:
            missing.append(f"a <details> reveal under {heading.removeprefix('## ').strip()!r}")
        if DRILL_MARKER in heading and (gap := missing_verification(heading, body)):
            missing.append(f"{gap} under {heading.removeprefix('## ').strip()!r}")

    orphans = sum(body.count(REVEAL) for heading, body in found if not is_exercise(heading))
    if orphans:
        missing.append(f"{orphans} <details> reveal(s) outside any exercise section")

    return missing


@pytest.mark.parametrize("module", modules(), ids=_ids(modules()))
def test_every_module_is_structurally_complete(module):
    """A module that renders is not the same as a module that teaches."""
    missing = missing_sections(module.read_text(), module.name)
    assert not missing, f"{module.name} is structurally incomplete, missing: {missing}"


# A drill's `sed -i '<script>' <file>`, after line continuations are joined.
# Everything after a shell operator is a separate command (`… && just
# course-rebuild`), so the invocation stops there.
_CONTINUATION = re.compile(r"\\\n\s*")
_SHELL_OPERATOR = ("&&", "||", ";", "|")

# `git checkout <path>` — how every drill undoes itself.
_CHECKOUT = re.compile(r"^git checkout ([A-Za-z0-9_./-]+)$", re.MULTILINE)


def sed_commands(text: str) -> list[tuple[str, str]]:
    """Every `(script, file)` a drill in `text` tells the learner to run.

    Parsed with `shlex` rather than a regex over the quoted script: the scripts
    contain `|`, `/` and spaces, and a regex that tries to find the closing
    quote gets the `s|a|b|` form wrong.
    """
    out = []
    for block in _FENCED.findall(text):
        for line in _CONTINUATION.sub(" ", block).splitlines():
            if not line.strip().startswith("sed "):
                continue
            tokens = shlex.split(line)
            for stop, token in enumerate(tokens):
                if token in _SHELL_OPERATOR:
                    tokens = tokens[:stop]
                    break
            args = [t for t in tokens[1:] if not t.startswith("-")]
            if len(args) == 2:
                out.append((args[0], args[1]))
    return out


@pytest.mark.parametrize("module", modules(), ids=_ids(modules()))
def test_every_drill_sed_still_changes_the_file_it_targets(module):
    """A drill that seeds no bug is worse than a drill that fails.

    `sed -i` exits 0 when its pattern matches nothing, so a drill whose target
    moved leaves the learner rebuilding a *healthy* sandbox and hunting a bug
    that was never seeded. Both of `02-loading-twice.md`'s drills sat in that
    state after `ingest/pipeline.py` was split into `ingest/sources/`: the paths
    they cite still exist, so `test_every_path_a_module_cites_exists` was green
    the whole time.

    Run rather than pattern-matched. sed scripts are POSIX BRE, where `(`, `{`
    and `+` mean something different than they do to `re`, and the drills use
    all three; reimplementing that dialect to check it is how the checker
    acquires its own bugs. A copy of the file and the real `sed` cannot be wrong
    about what the learner's `sed` will do.
    """
    inert = []
    for script, target in sed_commands(module.read_text()):
        source = project_root() / target
        if not source.exists():  # test_every_path_a_module_cites_exists owns this
            continue
        with tempfile.TemporaryDirectory() as tmp:
            copy = pathlib.Path(tmp) / source.name
            copy.write_bytes(source.read_bytes())
            done = subprocess.run(
                ["sed", "-i", script, str(copy)], capture_output=True, text=True, check=False
            )
            if done.returncode != 0:
                inert.append(f"{target}: sed rejected {script!r} — {done.stderr.strip()}")
            elif copy.read_bytes() == source.read_bytes():
                inert.append(f"{target}: {script!r} matches nothing, so the drill seeds no bug")
    assert not inert, f"{module.name} has drills that no longer bite:\n  " + "\n  ".join(inert)


@pytest.mark.parametrize("module", modules(), ids=_ids(modules()))
def test_every_drill_checks_out_the_file_it_edited(module):
    """The fix must undo the seed, in the same section.

    A drill that edits `ingest/sources/worldbank.py` and restores
    `ingest/pipeline.py` leaves the bug in the tree and reports success. One
    direction only: `git checkout` of a file no `sed` touched is how
    `00-setup.md` and `01-grain.md` demonstrate the loop over a hand edit.
    """
    unrestored = []
    for heading, body in sections(module.read_text()):
        edited = {target for _, target in sed_commands(body)}
        restored = set(_CHECKOUT.findall(body))
        if missing := sorted(edited - restored):
            unrestored.append(f"{heading.removeprefix('## ').strip()!r}: {missing}")
    assert not unrestored, (
        f"{module.name} seeds edits it never checks out again:\n  " + "\n  ".join(unrestored)
    )
