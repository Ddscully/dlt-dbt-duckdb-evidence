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

import re
import subprocess
from fnmatch import fnmatchcase
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PAGES_WORKFLOW = REPO_ROOT / ".github/workflows/pages.yml"
CI_WORKFLOW = REPO_ROOT / ".github/workflows/ci.yml"

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
    # `.github/actions/**` is on the *allow* side, not here: the setup action
    # installs the toolchain and exports the paths this build runs under, so a
    # change to it changes the site. `justfile` moved across for the same reason
    # — it used to sit here reading "pages.yml calls uv and dagster directly,
    # never a recipe", which stopped being true the day the build became
    # `just materialize-site`. That comment is why this test exists: the premise
    # a classification rests on goes stale silently, and the only symptom would
    # have been a site that stopped moving.
    #
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


# --------------------------------------------------------------------------- #
# ci.yml re-runs the manifest-gated tests, and that list is hand-maintained too
# --------------------------------------------------------------------------- #


# Anchored at column 0, so it matches a real module-level `pytestmark` and not a
# file that merely *mentions* one. The first draft searched for the two strings
# anywhere and flagged this module — which writes both, in the code below — as
# gated. A guard that reads source as text has to say where it is looking.
_GATED = re.compile(
    r"^pytestmark\s*=\s*pytest\.mark\.skipif\((?:.|\n)*?manifest_path", re.MULTILINE
)


def manifest_gated() -> set[str]:
    """Test files that skip themselves when `dbt/target/manifest.json` is absent."""
    return {
        path.name
        for path in (REPO_ROOT / "tests").glob("test_*.py")
        if _GATED.search(path.read_text())
    }


def re_run_after_parse() -> set[str]:
    """The files `ci.yml` names once the manifest exists.

    The unit-test step is a bare `uv run pytest` with nothing after it, so it
    contributes nothing here — which is the point: that is the run where these
    files skip.
    """
    named = re.findall(r"uv run pytest ([^\n]+)", CI_WORKFLOW.read_text())
    return {Path(arg).name for line in named for arg in line.split()}


def test_every_manifest_gated_test_file_is_re_run_after_dbt_parse():
    """A file that skips itself in CI's first step and is not named in its second
    runs **nowhere** in CI, and nothing says so.

    That is not hypothetical: `ci.yml` named only `test_definitions.py` while
    `test_asset_checks.py` and `test_documented_counts.py` carried the same
    skipif, so the asset-check bodies and every count cited in the docs went
    unchecked on every pull request — and both files' own headers claimed CI
    re-ran them. The skip is the loud-looking part and it is the honest half; the
    silence is in the step that was supposed to pick them back up.

    Compared as a set both ways, so adding a gated file without adding it here
    fails, and removing one from the workflow while it still skips fails too.
    """
    gated, re_run = manifest_gated(), re_run_after_parse()
    assert gated == re_run, (
        "ci.yml's post-parse pytest step and the manifest-gated test files disagree.\n"
        f"  gated but never re-run (they run nowhere in CI): {sorted(gated - re_run)}\n"
        f"  re-run but not gated (harmless, but the list is now wrong): {sorted(re_run - gated)}"
    )


# --------------------------------------------------------------------------- #
# A release is two assets, and a workflow that restores one must download both
# --------------------------------------------------------------------------- #


WORKFLOWS_DIR = REPO_ROOT / ".github/workflows"


def release_restoring_workflows() -> dict[str, str]:
    """`{"pages.yml": "<text>", …}` — the workflows that carry a release forward."""
    return {
        path.name: path.read_text()
        for path in sorted(WORKFLOWS_DIR.glob("*.yml"))
        if "scripts.restore_history" in path.read_text()
    }


def downloaded_assets(text: str) -> set[str]:
    """Every `--pattern <asset>` a workflow asks `gh release download` for."""
    return set(re.findall(r"--pattern\s+(\S+)", text))


def test_every_workflow_that_restores_a_release_downloads_both_of_its_assets():
    """A release is a database *and* a landing zone, and taking only the first is silent.

    `restore_history` finds the lakehouse beside the database rather than being
    told where it is, so a workflow that downloads `warehouse.duckdb` alone hands
    it a directory with no tarball in it — which is the module's documented
    "restoring nothing is a normal outcome" path, not an error. Nothing fails.

    What it costs is measured one layer down. Every workflow here rebuilds the
    marts from `raw`, and since `raw` moved into the DuckLake catalog the
    published database carries no weather rows at all — so the archive is not
    carried, `weather_watermark()` reads null, the ingest cold-starts at
    `WEATHER_COLD_START_YEARS`, and `marts.fct_country_weather_year` is built
    three years deep instead of the release's fifteen. The Weather page then
    renders correctly off a thin mart: green build, green checks, right shape,
    wrong depth. `pages.yml` shipped exactly that between the DuckLake move and
    2026-08-27.

    The asset names come from the code rather than from string literals here, so
    renaming either one fails this instead of quietly matching nothing.
    """
    from scripts import restore_history

    required = {Path(restore_history.DUCKDB_PATH).name, restore_history.LAKEHOUSE_ASSET}
    workflows = release_restoring_workflows()

    # The scan reads workflow text, so it has to be shown to find something —
    # rename the module and an empty result would pass every assertion below.
    assert {"pages.yml", "release-data.yml"} <= set(workflows), (
        f"the restore scan found {sorted(workflows)}; both of those restore a release"
    )

    missing = {
        name: sorted(required - downloaded_assets(text))
        for name, text in workflows.items()
        if not required <= downloaded_assets(text)
    }
    assert not missing, (
        "workflows that restore a release but do not download all of it: "
        f"{missing}\nEach asset needs its own `gh release download --pattern` line."
    )


# --------------------------------------------------------------------------- #
# The pipeline environment is defined once, in the setup action
# --------------------------------------------------------------------------- #


SETUP_ACTION = REPO_ROOT / ".github/actions/setup/action.yml"

# The three paths every layer resolves itself from. Absolute, and the first one
# is load-bearing rather than tidy — see the action's own comment.
PIPELINE_PATHS = ("WAREHOUSE_PATH", "LAKEHOUSE_DIR", "DAGSTER_HOME")


def _uncommented(text: str) -> str:
    """Workflow text with whole-line comments dropped.

    `pages.yml` names `DAGSTER_HOME` in prose — explaining that
    `.dagster/dagster.yaml` is a build input — and a scan that cannot tell a
    mention from an assignment would either fail on that sentence or be widened
    until it stopped seeing assignments too.
    """
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("#"))


def _assigns(text: str, name: str) -> bool:
    """`NAME: value` as a YAML key, or `NAME=value` in a run block."""
    return re.search(rf"^\s*{name}\s*[:=]", _uncommented(text), re.MULTILINE) is not None


def test_the_setup_action_defines_every_pipeline_path():
    """Vacuity guard, and it comes first: the two tests below assert an *absence*.

    If the action stopped exporting these the workflows would be clean of them,
    both of the assertions below would pass, and every job would run against
    `profiles.yml`'s relative default — which resolves to the same file until the
    day something runs from another directory.
    """
    action = SETUP_ACTION.read_text()
    missing = [name for name in PIPELINE_PATHS if f"{name}=$GITHUB_WORKSPACE" not in action]
    assert not missing, (
        f"{SETUP_ACTION.name} no longer exports {missing}; the absence tests below "
        "would then pass while nothing sets them at all"
    )


def test_no_workflow_defines_a_pipeline_path_itself():
    """These were set in all four workflows behind an identical six-line comment,
    and `WAREHOUSE_PATH` in exactly one of them.

    Both halves of that shape have already cost this repo something. When the
    landing zone moved into DuckLake all four needed the same new absolute
    `LAKEHOUSE_DIR` and none of them got it — dlt wrote the catalog from the repo
    root, dbt resolved its own copy from `dbt/`, and DuckLake compares the two as
    *strings*, so the same directory under two spellings was refused inside
    `dbt build`, one layer downstream of the layer that chose the spelling. No
    recipe could reproduce it, because every recipe exported the variable that
    hid it.

    A workflow that sets one of these again is not wrong on its own — it is a
    second definition, which is how the first one drifted.
    """
    offenders = {
        path.name: [name for name in PIPELINE_PATHS if _assigns(path.read_text(), name)]
        for path in sorted(WORKFLOWS_DIR.glob("*.yml"))
    }
    offenders = {name: found for name, found in offenders.items() if found}
    assert not offenders, (
        f"workflows defining a pipeline path themselves: {offenders}\n"
        "These come from .github/actions/setup, which is the one place they are stated."
    )


def test_every_workflow_that_runs_the_pipeline_uses_the_setup_action():
    """The other direction, and the one an absence test cannot reach.

    Dropping the `uses:` line makes a workflow inherit nothing: no `just` on
    PATH, no paths exported, and `profiles.yml`'s relative default quietly
    standing in for the two that matter. Nothing above notices, because a
    workflow that defines none of them is exactly what those tests want to see.
    """
    runners = {
        path.name
        for path in sorted(WORKFLOWS_DIR.glob("*.yml"))
        if "just materialize" in path.read_text()
    }
    assert runners == {"ci.yml", "nightly.yml", "pages.yml", "release-data.yml"}, (
        f"the scan for pipeline-running workflows found {sorted(runners)}; all four run it"
    )
    missing = sorted(
        name
        for name in runners
        if "./.github/actions/setup" not in (WORKFLOWS_DIR / name).read_text()
    )
    assert not missing, f"workflows running the pipeline without the setup action: {missing}"


# --------------------------------------------------------------------------- #
# Dependabot watches the composite action, not just the workflows
# --------------------------------------------------------------------------- #


DEPENDABOT = REPO_ROOT / ".github/dependabot.yml"
ACTIONS_DIR = REPO_ROOT / ".github/actions"


def composite_actions_with_dependencies() -> set[str]:
    """`{"/.github/actions/setup"}` — local actions that pin a third-party one.

    An action with no `uses:` of its own has nothing for Dependabot to bump and
    is not this test's business.
    """
    return {
        f"/.github/actions/{path.parent.name}"
        for path in ACTIONS_DIR.glob("*/action.yml")
        if re.search(r"^\s*(-\s*)?uses:", path.read_text(), re.MULTILINE)
    }


def dependabot_action_directories() -> list[str]:
    """The `directory`/`directories` values on the `github-actions` entry.

    Scanned rather than parsed, for `pages_allowlist`'s reason: PyYAML is not a
    declared dependency here — it arrives as dbt's transitive, and this repo has
    been bitten once by a runtime import that was really some other package's
    grand-child.
    """
    lines = DEPENDABOT.read_text().splitlines()
    found: list[str] = []
    in_entry = in_list = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- package-ecosystem:"):
            in_entry = "github-actions" in stripped
            in_list = False
            continue
        if not in_entry or stripped.startswith("#"):
            continue
        if stripped.startswith("directory:"):
            found.append(stripped.split(":", 1)[1].strip().strip("\"'"))
        elif stripped.startswith("directories:"):
            in_list = True
        elif in_list and stripped.startswith("- "):
            found.append(stripped[2:].strip().strip("\"'"))
        elif in_list:
            in_list = False
    return found


def test_the_dependabot_scan_reads_the_entry_it_thinks_it_does():
    """Vacuity guard, first for the reason the pages one is: the test below
    asserts a *covering*, and an empty read covers nothing but also finds nothing
    to cover if the actions scan is empty too."""
    assert "/" in dependabot_action_directories(), (
        "the github-actions entry in dependabot.yml no longer yields `/`; the scan "
        "has stopped matching the file rather than the file having changed"
    )
    assert composite_actions_with_dependencies(), (
        "no composite action with a `uses:` was found; either they are gone or the "
        "scan is looking in the wrong place"
    )


def test_dependabot_watches_every_composite_action_that_pins_one():
    """A bare `directory: /` scans `.github/workflows/` and nothing else.

    So moving the four workflows' shared setup into `.github/actions/setup` took
    the exactly-pinned `astral-sh/setup-uv` out of Dependabot's view with it —
    still pinned, no longer watched, and frozen with nothing to say so. Nothing
    goes red when a guard stops looking, which is why this is a test and not a
    comment in the config.

    `setup-uv` is the one that makes it matter: it stopped publishing moving
    major tags at v8, so a grouped monthly PR is the only thing that can ever
    bump it (see the `dependency-versions` skill).
    """
    watched = dependabot_action_directories()
    unwatched = sorted(
        directory
        for directory in composite_actions_with_dependencies()
        if not any(_matches(directory.lstrip("/"), pattern.lstrip("/")) for pattern in watched)
    )
    assert not unwatched, (
        f"composite actions Dependabot cannot see: {unwatched}\n"
        "Add the directory (or a glob covering it) to the github-actions entry's "
        "`directories:` list in .github/dependabot.yml."
    )
