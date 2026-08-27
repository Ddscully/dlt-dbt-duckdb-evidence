"""The course material and the project skills, checked against the repo they cite.

`docs/course/` and `.claude/skills/*/SKILL.md` quote file paths, `just` recipes
and module links out of the rest of the tree. None of that is executable, so it rots in exactly the way an
exposure does: the module still renders, the prose still reads correctly, and the
path it tells a learner to open was renamed six commits ago. A course that sends
someone to a file that is not there is worse than no course, because the reader
assumes they are the one who is wrong.

The same argument as `tests/test_exposures.py` and `tests/test_report.py`: an
assertion about the *outside* of a system needs a test on the outside of it.

No warehouse and no dbt manifest here — `just test` has neither, so everything
below reads markdown and the justfile as text.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

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
# Globbed, not listed. A hand-maintained list is the `definitions.py` failure
# CLAUDE.md documents: the omission is not an error, it is simply a file nothing
# checks. A skill added later is guarded whether or not anyone remembers this.
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

# A backticked path, e.g. `dbt/models/marts/dim_country_year.sql`. Anchored on the
# citable roots so prose like `country_iso3` and `PASS=402` can't match.
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
    `CITABLE_ROOTS`, and `reports/` is why the general form is needed —
    `reports/pages/` and `reports/sources/` are source, while `reports/build/`,
    `reports/node_modules/` and `reports/.evidence/` are output of
    `just report`. Splitting that by prefix would be a second list to drift;
    asking git is the same question the .gitignore already answers.

    Without this the guard passes on a developer's machine, where the site has
    been built, and fails in CI — the exact shape of rot it exists to catch.

    Every path is asked about twice, with and without a trailing slash, because
    `git check-ignore` is not a pure function of the .gitignore. A directory-only
    pattern — `dbt/target/`, written with the slash — matches only a path git can
    see is a directory, and it asks the filesystem to decide. So a cited
    `dbt/target` was exempt on a machine that had built and exempt nowhere else:
    green locally, red on a fresh checkout, which is this helper's own failure
    mode arriving inside the exemption meant to prevent it. A trailing slash on
    the probe answers the directory question without the filesystem.
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
# slug drops it — which is why `## The lake (`lake/archive.py`)` anchors as
# `#the-lake-lakearchivepy`, backticks, brackets and slashes all gone.
_NOT_IN_SLUG = re.compile(r"[^a-z0-9 \-_]")

# Any heading level, with its text captured. Deliberately not `_HEADING`, which
# this module already uses for `^## .*$` in the structural checks below — the
# second definition silently won, and `m.group(1)` then raised on a pattern with
# no group at all. A collision, not a typo.
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

    `docs/WAREHOUSE.md` pointed at `CLAUDE.md#cbam-exposure-…` for three days
    after that section was split out into `.claude/skills/compliance-models/`.
    Nothing could have said so: the two citation tests above check backticked
    *paths* and `just` recipes, and the path in a `](…)` link was still correct —
    it was the `#fragment` after it that named a heading no longer there. Every
    split of CLAUDE.md is a chance to do it again, and there have been two.
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

    Same failure as `_ROUTES` reachability and `seen > 35` in
    `tests/test_documented_counts.py`: a scanner whose pattern drifts reports no
    findings, which is indistinguishable from a clean tree.

    Non-emptiness and nothing else. A floor on the *number* of anchors would go
    red on a legitimate deletion — there are only four, and removing one is a
    normal edit — which is a worse trade here than in
    `tests/test_documented_counts.py`, where claims only ever accumulate.

    A second assertion, that at least one anchor is written `../`-relative and
    so exercises the `doc.parent / target` join, was written and then dropped:
    resolving from the repo root instead fails
    `test_every_cross_file_anchor_resolves` with "(no such file)" on both docs/
    links, so it was never the assertion doing the catching — and it would fire
    on a tree whose only anchors happened to sit in the root, which is a correct
    state. A guard whose effect another test already has is not evidence either
    way; the rule is CLAUDE.md's, applied to itself.
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
    course exists to break." All five drills written so far honour it, and
    nothing enforces it — which is the exact shape this file exists to catch, one
    level up. An unenforced promise in the index is a claim about the outside of
    the material, and it rots the same way a renamed path does.

    Two levels of strictness, because the literal alone is not worth much: a
    drill could carry the words and no command, which is the promise broken in
    the way that reads as kept.

    - the `**Verification.**` marker is present, and
    - a fenced block follows it, before the reveal.

    "Before the reveal" is doing real work and is not an ordering rule for its
    own sake. Every reveal is full of fenced SQL, so an unbounded search finds a
    block whatever the drill itself carries and the second check measures
    nothing. Bounding it at `<details>` is what makes the fenced half an
    assertion about the drill rather than about the answer.

    Deliberately *not* checked: that the marker sits on a line of its own.
    `01-grain.md` writes "**Verification.** When you think it is fixed:" with the
    block on the next line, which is good prose and a bad thing to forbid.

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

    Two rules beyond the boilerplate, and the second is the interesting one:

    - **`00-setup.md` is exempt from the exercise rules by name.** It is setup
      and deliberately carries no drills. Exempting it by name rather than by
      "this module happens to have no markers" matters — the latter excuses every
      module that forgot to write any, which is the case the check exists for.
    - **One reveal per exercise marker, enforced as a section-level bijection:**
      every `##` section whose heading is marked must contain at least one
      `<details>`, and no `<details>` may sit outside a marked section. Strict in
      both directions — an exercise with no answer fails, and so does an answer
      with no question.

      Not `count(marker) == count(<details>)`, which sounds like the same rule
      and is not: `01-grain.md` has three marked headings and five reveals,
      because its design-defence section asks (a), (b) and (c) and answers each.
      Counting would fail the one module we know is complete, which is an
      argument about the rule rather than about the module.

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
