"""`pages.yml`'s path allowlist classifies every tracked file, both ways.

That workflow is a full ingest -> dbt -> Polars -> lake -> Evidence run against
the live public APIs, and it used to trigger on every push to `main` with no
filter at all — so a README-only commit cost the same Actions minutes and the
same API load as a data change. 23 of the 94 commits on main were that.

The filter is an allowlist rather than `paths-ignore`, because the dashboard
*is* markdown (`reports/pages/`) and a blanket `**.md` ignore would stop
republishing the site exactly when a page changed. An allowlist gets the
mistakes the right way round, but it is still a hand-maintained list that has to
agree with the tree — which in this repo means it gets a test. A new top-level
directory that the site is built from would otherwise be omitted here in
silence, and the only symptom would be a site that stopped moving.

So every tracked file must be claimed by exactly one side: the allowlist in the
workflow, or `NOT_A_SITE_INPUT` below. A new path claimed by neither is a red
test asking someone to decide, which is the whole point.
"""

from __future__ import annotations

import subprocess
from fnmatch import fnmatchcase
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PAGES_WORKFLOW = REPO_ROOT / ".github/workflows/pages.yml"

# Tracked paths the published site is *not* built from. Every one of these has
# a reason, and the reason is never "it is markdown" — `reports/pages/*.md` is
# the dashboard.
NOT_A_SITE_INPUT = (
    "docs/**",  # prose about the warehouse, read by people not by the build
    "tests/**",  # this job runs no tests; ci.yml does
    "data/**",  # a .gitkeep; everything real under it is gitignored
    ".claude/**",  # agent skills and plugin settings
    "README.md",
    "CLAUDE.md",
    "LICENSE",
    "justfile",  # pages.yml calls uv and dagster directly, never a recipe
    ".gitignore",
    ".pre-commit-config.yaml",  # lint gates, and this job does not lint
    ".sqlfluff",
    ".github/dependabot.yml",
    # Community-health files: the front door for contributors, not for the
    # build. They arrived after the filter was written and this test is what
    # named them.
    ".github/CODE_OF_CONDUCT.md",
    ".github/CONTRIBUTING.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/ISSUE_TEMPLATE/**",
    # The other three workflows. Listed one by one rather than as
    # `.github/workflows/*`: a *new* workflow should land here unclassified and
    # make someone say whether the site is built from it.
    ".github/workflows/ci.yml",
    ".github/workflows/nightly.yml",
    ".github/workflows/release-data.yml",
)


def pages_allowlist() -> list[str]:
    """The `paths:` entries under `on: push:` in `pages.yml`.

    Scanned rather than parsed with PyYAML, which is not a declared dependency
    here — it arrives as dbt's transitive, and this repo has already been bitten
    once by a runtime import that was really some other package's grand-child
    (see the pyarrow bullet in CLAUDE.md). The block is a flat list of quoted
    scalars, so a scan is honest about what it can read; `test_the_scan_reads
    _the_block_it_thinks_it_does` is the vacuity guard, because a scanner whose
    pattern stops matching passes by not looking.
    """
    lines = PAGES_WORKFLOW.read_text().splitlines()
    if "    paths:" not in lines:
        # Deliberately empty rather than raising: an empty read is exactly what
        # the vacuity guard is written to notice, and its message names the
        # problem where a bare ValueError traceback would not.
        return []
    start = lines.index("    paths:")
    found = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if not stripped.startswith("- "):
            break  # dedented out of the list — the next key
        found.append(stripped[2:].strip().strip('"').strip("'"))
    return found


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.split()


def _matches(path: str, pattern: str) -> bool:
    # GitHub's `dir/**` matches everything under `dir`; fnmatch's `*` crosses
    # `/` already, so the two agree on the shapes used here. `fnmatchcase`
    # because plain `fnmatch` takes the platform's case rules and this
    # comparison must not differ between a mac and the runner.
    return fnmatchcase(path, pattern)


def test_the_scan_reads_the_block_it_thinks_it_does():
    """Vacuity guard: an empty or truncated read makes every test below pass."""
    allow = pages_allowlist()
    assert len(allow) >= 10, f"pages.yml paths: block read as {allow}"
    assert "reports/**" in allow, "the site's own source is not in its allowlist"
    assert not any(p.startswith("-") or p.endswith(":") for p in allow), allow


def test_every_tracked_file_is_claimed_by_exactly_one_side():
    allow = pages_allowlist()
    unclassified, both = [], []
    for path in tracked_files():
        built_from = [p for p in allow if _matches(path, p)]
        excluded = [p for p in NOT_A_SITE_INPUT if _matches(path, p)]
        if built_from and excluded:
            both.append(path)
        elif not built_from and not excluded:
            unclassified.append(path)

    assert not unclassified, (
        "tracked paths neither in pages.yml's `paths:` allowlist nor in "
        "NOT_A_SITE_INPUT — decide whether the published site is built from "
        f"them: {sorted(unclassified)}"
    )
    # Overlap is not a harmless duplicate. A deny pattern that also covers an
    # allowed path would keep this file green if the allow entry were deleted,
    # which is the drift the whole test exists to catch.
    assert not both, f"claimed by both lists, so neither is load-bearing: {sorted(both)}"


@pytest.mark.parametrize("side", ["allow", "deny"])
def test_no_pattern_has_outlived_the_path_it_named(side):
    """The stale direction, which nothing else could surface.

    A rename fires the *unclassified* assertion above and never reaches this
    one — the new path is uncovered, so the first test wins and the orphaned
    pattern sits there measuring nothing. Same shape as the RAW_DESCRIPTIONS
    guard in `test_definitions.py`: isolating the stale branch takes its own
    assertion, not a second look at the same mutation.
    """
    patterns = pages_allowlist() if side == "allow" else list(NOT_A_SITE_INPUT)
    tracked = tracked_files()
    orphaned = [p for p in patterns if not any(_matches(f, p) for f in tracked)]
    assert not orphaned, f"{side} patterns matching no tracked file: {orphaned}"
