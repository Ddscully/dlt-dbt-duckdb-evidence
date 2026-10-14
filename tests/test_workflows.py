"""`pages.yml`'s path allowlist classifies every tracked file, both ways.

That workflow rebuilds the warehouse and the site, from the live public APIs
whenever the push can change what lands in `raw`, so a docs-only push should not
trigger it. The filter is an
allowlist rather than `paths-ignore`, because the dashboard *is* markdown
(`reports/pages/`) and a blanket `**.md` ignore would stop republishing the site
exactly when a page changed. But a hand-maintained allowlist can omit a new
build input in silence, and the only symptom is a site that stops moving.

So every tracked file must be claimed by exactly one side: the allowlist in the
workflow, or `NOT_A_SITE_INPUT` below. A new path claimed by neither is a red
test asking someone to decide.
"""

from __future__ import annotations

import inspect
import re
import subprocess
from fnmatch import fnmatchcase
from pathlib import Path

import pytest

from lake import lakehouse

REPO_ROOT = Path(__file__).resolve().parent.parent
PAGES_WORKFLOW = REPO_ROOT / ".github/workflows/pages.yml"
CI_WORKFLOW = REPO_ROOT / ".github/workflows/ci.yml"

# Tracked paths the site is not built from; the reason is never "it is
# markdown" — reports/pages/*.md is the dashboard.
NOT_A_SITE_INPUT = (
    "docs/**",  # prose about the warehouse, read by people not by the build
    "tests/**",  # this job runs no tests; ci.yml does
    "data/**",  # a .gitkeep; everything real under it is gitignored
    ".agents/**",  # agent skills, for every agent
    ".claude/**",  # Claude Code's plugin settings, and the skills symlink
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "LICENSE",
    ".gitignore",
    ".env.example",  # a template for a local .env, which no runner has
    ".pre-commit-config.yaml",  # lint gates, and this job does not lint
    ".sqlfluff",
    ".github/dependabot.yml",
    # Community-health files: the front door for contributors, not the build.
    ".github/CODE_OF_CONDUCT.md",
    ".github/CONTRIBUTING.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/ISSUE_TEMPLATE/**",
    # `.github/actions/**` and `justfile` are on the allow side: they define the
    # `just materialize-site` build. Workflows are listed one by one, not as a
    # glob, so a new one lands unclassified and forces a decision.
    ".github/workflows/ci.yml",
    ".github/workflows/live.yml",
    ".github/workflows/release-data.yml",
    # Seed builders' output is checked in (dbt/** covers it); the rest are live
    # or read-only.
    "scripts/**",
    # The analyst's tools read the built marts; nothing in the asset graph calls them.
    "agent/**",
    # Backing services: no runner starts them; the setup action places the
    # landing zone on disk regardless.
    "compose.yaml",
    "deploy/**",
    # The image, for the same reason: the site builds on the runner, never in a container.
    "Dockerfile",
    ".dockerignore",
    # Provisions an editor only; the site still builds on the runner.
    ".devcontainer/**",
)


def pages_allowlist() -> list[str]:
    """The `paths:` entries under `on: push:` in `pages.yml`.

    Scanned rather than parsed with PyYAML, which is not a declared dependency
    — it arrives as dbt's transitive (see the pyarrow bullet in the
    `dependency-versions` skill for why that matters). The block is a flat list
    of quoted scalars, so a scan can read it, and the first test below is the
    vacuity guard.
    """
    lines = PAGES_WORKFLOW.read_text().splitlines()
    if "    paths:" not in lines:
        # Empty rather than raising: the vacuity guard below reports this.
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
    # fnmatch's `*` crosses `/`, matching GitHub's `dir/**`; fnmatchcase because
    # plain fnmatch takes the platform's case rules, and this must not differ.
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
    assert not both, (
        "claimed by both lists, so neither is load-bearing — a deny pattern "
        "covering an allowed path would stay green if the allow entry were "
        f"deleted: {sorted(both)}"
    )


@pytest.mark.parametrize("side", ["allow", "deny"])
def test_no_pattern_has_outlived_the_path_it_named(side):
    """The stale direction, which nothing else could surface.

    A rename fires the *unclassified* assertion above and never reaches this
    one — the new path is uncovered, so the first test wins and the orphaned
    pattern measures nothing. Isolating the stale branch takes its own
    assertion.
    """
    patterns = pages_allowlist() if side == "allow" else list(NOT_A_SITE_INPUT)
    tracked = tracked_files()
    orphaned = [p for p in patterns if not any(_matches(f, p) for f in tracked)]
    assert not orphaned, f"{side} patterns matching no tracked file: {orphaned}"


# --------------------------------------------------------------------------- #
# ci.yml re-runs the manifest-gated tests, and that list is hand-maintained too
# --------------------------------------------------------------------------- #


# Anchored at column 0: matches a real pytestmark, not a file merely mentioning one.
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

    **Comment lines are dropped first**, and that is not tidiness. Scanning the
    whole file reads a comment explaining *why a job runs no pytest command* as
    a pytest command, and its prose words become expected test filenames. A guard
    that a sentence about it can break is a guard that discourages writing the
    sentence.
    """
    lines = [
        line for line in CI_WORKFLOW.read_text().splitlines() if not line.lstrip().startswith("#")
    ]
    named = re.findall(r"uv run pytest ([^\n]+)", "\n".join(lines))
    return {Path(arg).name for line in named for arg in line.split()}


def test_every_manifest_gated_test_file_is_re_run_after_dbt_parse():
    """A file that skips itself in CI's first step and is not named in its second
    runs **nowhere** in CI, and nothing says so.

    The skips show in every build log and read as normal; the silence is in the
    step that should pick the files back up.

    Compared as a set both ways, so adding a gated file without adding it to
    the workflow fails, and removing one from the workflow while it still skips
    fails too.
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
        if "publish.restore_history" in path.read_text()
    }


def downloaded_assets(text: str) -> set[str]:
    """Every `--pattern <asset>` a workflow asks `gh release download` for."""
    return set(re.findall(r"--pattern\s+(\S+)", text))


def test_every_job_sets_a_timeout_of_its_own():
    """GitHub's default is six hours, and a run that hangs spends every one of
    them: the weather re-fetch in run 37100640862 slept until it was cancelled at
    six hours, with nothing in the log. A job missing its own `timeout-minutes`
    inherits that default silently."""
    yaml = pytest.importorskip("yaml")
    workflows = sorted(WORKFLOWS_DIR.glob("*.yml"))
    assert workflows, f"no workflows under {WORKFLOWS_DIR}"
    missing = [
        f"{path.name}:{job}"
        for path in workflows
        for job, spec in yaml.safe_load(path.read_text())["jobs"].items()
        if not isinstance(spec.get("timeout-minutes"), int) or spec["timeout-minutes"] >= 360
    ]
    assert not missing, f"jobs on GitHub's six-hour default: {missing}"


def test_a_job_that_reports_its_own_failure_still_reports_a_timeout():
    """A step that hits its own `timeout-minutes` *fails*, so the steps gated on
    `failure()` run; a job that hits its timeout is *cancelled*, and they are
    skipped (actions/runner, `StepsRunner.cs`; run 37100640862 ended
    `cancelled`). live.yml's issue step and ci.yml's service logs exist for the
    run that goes wrong, and a hang is the likeliest way to go wrong. So every
    command before such a step carries a timeout, and together they leave the
    job's own timeout room to spare: otherwise the job's fires first."""
    yaml = pytest.importorskip("yaml")
    problems = []
    for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        for job, spec in yaml.safe_load(path.read_text())["jobs"].items():
            steps = spec.get("steps", [])
            gated = [i for i, step in enumerate(steps) if "failure()" in str(step.get("if", ""))]
            if not gated:
                continue
            commands = [step for step in steps[: gated[0]] if "run" in step]
            untimed = [
                step.get("name", step["run"]) for step in commands if "timeout-minutes" not in step
            ]
            total = sum(step.get("timeout-minutes", 0) for step in commands)
            if untimed:
                problems.append(f"{path.name}:{job} runs with no timeout of their own: {untimed}")
            elif total >= spec["timeout-minutes"]:
                problems.append(
                    f"{path.name}:{job} step timeouts sum to {total}, not under the job's "
                    f"{spec['timeout-minutes']}"
                )
    assert not problems, problems


def test_every_workflow_that_restores_a_release_downloads_both_of_its_assets():
    """A release is a database *and* a landing zone, and taking only the first is silent.

    `restore_history` finds the lakehouse beside the database rather than being
    told where it is, so a workflow that downloads `warehouse.duckdb` alone hands
    it a directory with no tarball in it — which is the module's documented
    "restoring nothing is a normal outcome" path, not an error. Nothing fails.

    The cost shows one layer down. `raw` lives in the DuckLake catalog, so the
    database alone carries no weather rows: `weather_watermark()` reads null,
    the ingest cold-starts at `WEATHER_COLD_START_YEARS`, and
    `marts.fct_country_weather_year` is built three years deep instead of the
    release's full archive. The Weather page renders correctly off the thin
    mart — green build, green checks, right shape, wrong depth.

    `live.yml` is the one that matters most since `pages.yml` builds from its
    cached landing zone: a thin archive there is a thin Weather page on every
    push until the next refresh.

    The asset names come from the code rather than from string literals here, so
    renaming either one fails this instead of quietly matching nothing.
    """
    from publish import restore_history

    required = {Path(restore_history.DUCKDB_PATH).name, restore_history.LAKEHOUSE_ASSET}
    workflows = release_restoring_workflows()

    assert {"pages.yml", "live.yml", "release-data.yml"} <= set(workflows), (
        f"the restore scan found {sorted(workflows)}; all three restore a "
        "release — renaming the module would silently empty this scan"
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


def test_the_release_never_tolerates_a_failed_download():
    """A failed download in `release-data.yml` must fail the release.

    `restore_history` reads a missing tarball as nothing to carry, so the verify
    step's "published rows >= carried rows" compares against zero and passes: a
    transient `gh` error would publish a cold-started weather archive, green.
    `pages.yml` soft-fails the same step deliberately, and the release once copied
    its `|| true` — the absent asset is decided by asking the release what it
    holds, never by swallowing the error.
    """
    text = (WORKFLOWS_DIR / "release-data.yml").read_text()
    commands = text.replace("\\\n", " ")  # one line per continued command
    downloads = [line for line in commands.splitlines() if "gh release download" in line]

    # Named rather than counted: the restore's two assets, and the manifest the
    # comparison step reads. A failed manifest download would skip the only check
    # that sees a table shrink between releases.
    expected = {"warehouse.duckdb", "lakehouse.tar.gz", "manifest.json"}
    assert downloaded_assets("\n".join(downloads)) == expected, (
        f"expected downloads of {sorted(expected)}, found {downloads}"
    )
    tolerated = [line.strip() for line in downloads if "||" in line]
    assert not tolerated, f"release-data.yml tolerates a failed download: {tolerated}"
    assert "continue-on-error" not in text, "a soft-failing step would tolerate it too"


# --------------------------------------------------------------------------- #
# pages.yml builds from the landing zone live.yml caches
# --------------------------------------------------------------------------- #

CACHE_ACTIONS = ("actions/cache/save@", "actions/cache/restore@")
PACKAGE = "modern_data_stack"


def _workflow(name: str) -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load((WORKFLOWS_DIR / name).read_text())


def _steps(workflow: dict) -> list[dict]:
    return [step for job in workflow["jobs"].values() for step in job.get("steps", [])]


def test_live_caches_the_landing_zone_where_pages_looks_for_it():
    """The hand-off between the two workflows is two strings, and a mismatch is green.

    actions/cache matches on the path as well as the key, so a cache saved under
    another path or prefix is never found: every build falls back to the live
    path, which builds and deploys exactly as before the cache existed. The one
    trace is a line in each run's summary. So both workflows read the same two
    env names, set identically; the path is the landing zone the setup action
    points `LAKEHOUSE_DIR` at; and every key is stamped with the epoch second
    that `pages.yml` reads the cache's age from and the commit it diffs from.
    """
    pages, live = _workflow("pages.yml"), _workflow("live.yml")
    for name in ("RAW_CACHE_PATH", "RAW_CACHE_PREFIX"):
        assert pages["env"][name] == live["env"][name], f"{name} differs between the two"
    path = pages["env"]["RAW_CACHE_PATH"]
    assert f'LAKEHOUSE_DIR=$GITHUB_WORKSPACE/{path}"' in SETUP_ACTION.read_text(), (
        f"the cache holds {path}, which is not the landing zone the setup action exports"
    )

    for name, workflow in (("pages.yml", pages), ("live.yml", live)):
        steps = _steps(workflow)
        cached = [s for s in steps if str(s.get("uses", "")).startswith(CACHE_ACTIONS)]
        assert cached, f"{name} has no actions/cache step"
        assert all(s["with"]["path"] == "${{ env.RAW_CACHE_PATH }}" for s in cached), name
        stamps = [s for s in steps if s.get("id") == "stamp"]
        assert stamps, f"{name} saves no cache entry"
        assert all(
            "key=${RAW_CACHE_PREFIX}$(date +%s)-${GITHUB_RUN_ID}-${GITHUB_SHA}" in s["run"]
            for s in stamps
        ), name

    (lookup,) = [s for s in _steps(pages) if s.get("id") == "lookup"]
    assert lookup["with"]["restore-keys"] == "${{ env.RAW_CACHE_PREFIX }}"


def test_no_workflow_reads_a_failed_download_as_an_absent_asset():
    """`|| true` on a release download turns a transient error into "this
    release has no such asset", which `restore_history` reads as nothing to
    carry. In `live.yml` and a live `pages.yml` build that is worse than in the
    release: the borrow step still reports `carried`, and the cold-started
    weather archive is cached for every build until the next refresh. Every
    workflow that restores asks the release what it holds instead.
    """
    problems = {}
    for name, text in release_restoring_workflows().items():
        commands = text.replace("\\\n", " ")  # one line per continued command
        tolerated = [
            line.strip()
            for line in commands.splitlines()
            if "gh release download" in line and "||" in line
        ]
        if tolerated:
            problems[name] = tolerated
    assert not problems, f"downloads whose failure reads as an absent asset: {problems}"


def test_every_build_is_diffed_from_the_commit_that_built_its_cache():
    """The cache is only as new as the code that fetched it, so that commit is
    the one to diff from, whatever started the run.

    Diffed from the last successful run, a cached build or a live one whose
    borrow soft-failed saves no cache but still becomes the base, and an
    `ingest/` change before it reaches the site over a landing zone the old
    code fetched. Diffed from the push's own `before`, so does one whose run
    the concurrency group cancelled while it waited. Diffed on a push alone,
    the Monday rebuild and a plain dispatch take any cache, including one a
    `live.yml` run that started before the change finished saving. Diffed with
    renames on, a file moved out of `ingest/` is listed only where it went.
    """
    (mode,) = [s for s in _steps(_workflow("pages.yml")) if s.get("id") == "mode"]
    run = mode["run"]
    assert 'git diff --no-renames --name-only "$sha" "$GITHUB_SHA"' in run
    assert "gh run list" not in run, "diffed from the last successful run"
    assert "github.event.before" not in str(mode), "diffed from the push, not the cache"
    assert "push)" not in run, "diffed only when a push started the run"
    assert "event_name" not in str(mode) and "$EVENT" not in run, (
        "the decision branches on what started the run, so some event skips the diff"
    )


def test_no_workflow_uploads_the_warehouse_or_the_landing_zone_in_the_clear():
    """Everything under `data/` holds clear customer ids: the warehouse, where the
    id is pseudonymised only at the export, and the landing zone. Anyone signed
    in to GitHub can download a public repository's artifacts, and `live.yml`
    once uploaded `data/warehouse.duckdb` itself. Its artifact is the copy
    `pseudonymised_copy` writes, and nothing else.
    """
    uploads: dict[str, list[str]] = {}
    for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        for step in _steps(_workflow(path.name)):
            if str(step.get("uses", "")).startswith("actions/upload-artifact@"):
                uploads.setdefault(path.name, []).append(str(step["with"]["path"]).strip())
    assert "live.yml" in uploads, f"the upload scan found {uploads}"
    clear = {name: [p for p in paths if p.startswith("data/")] for name, paths in uploads.items()}
    assert not any(clear.values()), f"artifacts uploaded from under data/: {clear}"

    (copy,) = [
        s for s in _steps(_workflow("live.yml")) if "pseudonymised_copy" in str(s.get("run"))
    ]
    assert all(path in copy["run"] for path in uploads["live.yml"]), (
        f"live.yml uploads {uploads['live.yml']}, which its pseudonymising step does not write"
    )


def _live_paths() -> list[str]:
    (mode,) = [s for s in _steps(_workflow("pages.yml")) if s.get("id") == "mode"]
    return mode["env"]["LIVE_PATHS"].split()


def _package_modules_reached_from(*dirs: str) -> set[str]:
    """`src/modern_data_stack/<m>.py` for each package module the code under
    `dirs` imports, directly or through another package module."""
    import ast

    def imported(path: Path, inside_package: bool) -> set[str]:
        found = set()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                found |= {
                    a.name.split(".")[1] for a in node.names if a.name.startswith(f"{PACKAGE}.")
                }
            elif isinstance(node, ast.ImportFrom):
                relative = inside_package and node.level == 1
                if relative or node.module == PACKAGE:
                    found |= (
                        {node.module} if relative and node.module else {a.name for a in node.names}
                    )
                elif node.module and node.module.startswith(f"{PACKAGE}."):
                    found.add(node.module.split(".")[1])
        return found

    package_dir = REPO_ROOT / "src" / PACKAGE
    todo = set()
    for d in dirs:
        for path in (REPO_ROOT / d).rglob("*.py"):
            todo |= imported(path, inside_package=False)
    reached: set[str] = set()
    while todo:
        module = todo.pop()
        if module in reached or not (package_dir / f"{module}.py").exists():
            continue
        reached.add(module)
        todo |= imported(package_dir / f"{module}.py", inside_package=True)
    return {f"src/{PACKAGE}/{m}.py" for m in reached}


def test_a_push_goes_live_whenever_it_can_change_what_lands_in_raw():
    """A push that edits extraction code but takes the cached path is not a failure.

    It deploys the new code over last week's landing zone, green, and the change
    first meets the live sources at the next refresh: the delay the live path
    exists to avoid, with nothing to say it happened. `LIVE_PATHS` is a list by
    hand, so it is held to the imports: every package module `ingest/` and
    `lake/` reach, directly or through another, must send a push live.
    """
    live = _live_paths()
    # `uv.lock` for the catalog: a newer DuckDB may not read one an older wrote.
    assert {"ingest/**", "lake/**", "uv.lock"} <= set(live), f"LIVE_PATHS read as {live}"
    tracked = tracked_files()
    stale = [p for p in live if not any(_matches(f, p) for f in tracked)]
    assert not stale, f"LIVE_PATHS entries matching no tracked file: {stale}"

    reached = _package_modules_reached_from("ingest", "lake")
    assert f"src/{PACKAGE}/ratelimit.py" in reached, f"the import scan read {sorted(reached)}"
    missing = sorted(m for m in reached if not any(_matches(m, p) for p in live))
    assert not missing, (
        f"package modules the extraction code imports, absent from LIVE_PATHS: {missing}"
    )


def test_every_path_that_sends_a_push_live_also_triggers_the_build():
    """A live path outside the trigger list is dead: a push touching only it
    never starts the workflow, so it never reaches the decision at all."""
    allow, live = pages_allowlist(), _live_paths()
    untriggered = sorted(
        f
        for f in tracked_files()
        if any(_matches(f, p) for p in live) and not any(_matches(f, p) for p in allow)
    )
    assert not untriggered, f"sent live, but not in pages.yml's paths: {untriggered}"


# --------------------------------------------------------------------------- #
# The pipeline environment is defined once, in the setup action
# --------------------------------------------------------------------------- #


SETUP_ACTION = REPO_ROOT / ".github/actions/setup/action.yml"

# The three paths every layer resolves from; absolute, first one load-bearing
# (see the setup action).
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
    """One definition, in the setup action, so a new path reaches every workflow.

    Four copies drift: a new variable lands in some and not others. For
    `LAKEHOUSE_DIR` that is loud but misplaced — DuckLake compares `data_path`
    as a *string*, so dlt (from the repo root) and dbt (from `dbt/`) spelling
    one directory two ways is refused inside `dbt build`, a layer downstream of
    the cause. A workflow setting one of these again is not wrong on its own; it
    is a second definition, which is how the first one drifts.
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
    assert runners == {"ci.yml", "live.yml", "pages.yml", "release-data.yml"}, (
        f"the scan for pipeline-running workflows found {sorted(runners)}; all four run it"
    )
    missing = sorted(
        name
        for name in runners
        if "./.github/actions/setup" not in (WORKFLOWS_DIR / name).read_text()
    )
    assert not missing, f"workflows running the pipeline without the setup action: {missing}"


# --------------------------------------------------------------------------- #
# dlt reads a workflow's environment as its own configuration
# --------------------------------------------------------------------------- #


def dlt_argument_names() -> set[str]:
    """Every argument dlt may resolve from the environment, as an env var name.

    dlt fills a source's or resource's arguments from config, and the last key it
    tries is the bare upper-cased name. So `WEATHER_YEARS` in a step's `env:` is
    read as `public_indicators(weather_years=…)` by everything the step runs.
    """
    from dlt.extract.resource import DltResource

    from ingest import pipeline

    names = set(inspect.signature(pipeline.public_indicators).parameters)
    for resource in vars(pipeline).values():
        if isinstance(resource, DltResource):
            names |= set(inspect.signature(resource).parameters)
    return {name.upper() for name in names}


def test_the_dlt_argument_scan_reads_what_it_thinks_it_does():
    """Vacuity guard: an empty set would leave the test below nothing to find."""
    assert {"WEATHER_YEARS", "WDI_YEARS", "YEARS"} <= dlt_argument_names()


# The env template's entries are commented out for a person to uncomment, so a
# `#NAME=` there is a variable waiting to be set, not prose.
ENV_TEMPLATE = REPO_ROOT / ".env.example"

# Every tracked file that sets the environment the pipeline runs under.
ENVIRONMENT_FILES = (
    *sorted(WORKFLOWS_DIR.glob("*.yml")),
    SETUP_ACTION,
    REPO_ROOT / "compose.yaml",
    REPO_ROOT / "justfile",
    ENV_TEMPLATE,
)


def _sets_env(text: str, name: str, *, template: bool = False) -> bool:
    """Every spelling these files set a variable in, where `_assigns` knows two.

    A YAML key or a dotenv line (`NAME:`, `NAME=`), a line echoed into
    `$GITHUB_ENV` (`echo "NAME=…"`, which is how the setup action writes the
    pipeline paths), and an export from a shell or from `just` (`export NAME=`,
    `export NAME :=`).
    """
    if not template:
        text = _uncommented(text)
    spellings = (
        rf"^\s*{'#?' if template else ''}{name}\s*[:=]",
        rf"\becho\s+[\"']?{name}=",
        rf"\bexport\s+{name}\s*:?=",
    )
    return any(re.search(pattern, text, re.MULTILINE) for pattern in spellings)


@pytest.mark.parametrize(
    "text, template",
    [
        ("env:\n  YEARS: 2010\n", False),
        ("run: |\n  YEARS=2010 just materialize\n", False),
        ('run: |\n  {\n    echo "YEARS=2010"\n  } >> "$GITHUB_ENV"\n', False),
        ("run: |\n  export YEARS=2010\n", False),
        ('export YEARS := "2010"\n', False),
        ("#YEARS=2010\n", True),
    ],
)
def test_the_environment_scan_sees_every_spelling(text, template):
    """Vacuity guard for the scan below: `_assigns` alone missed the `$GITHUB_ENV`
    echo, which is the setup action's own way of exporting, and every export."""
    assert _sets_env(text, "YEARS", template=template)


def test_the_environment_scan_skips_a_comment_outside_the_template():
    """The other side: a workflow explaining a name in prose is not setting it."""
    assert not _sets_env('# echo "YEARS=2010" would reach dlt as an argument\n', "YEARS")


def test_no_workflow_sets_a_variable_dlt_reads_as_an_argument():
    """A workflow's own variable must not share a name with a dlt argument.

    The release's `weather_years` input once reached its step as `WEATHER_YEARS`,
    and every Dagster import under that step failed on it: dlt could not coerce
    `"2017 2026"` into the source's year tuple. It passed every local run, where
    nothing sets the variable, and failed on the input's first dispatch.

    Not only the workflows: the setup action, the compose stack, the justfile and
    the env template all set the environment Dagster imports under. A developer's
    own `.env` is untracked and unread here.
    """
    names = dlt_argument_names()
    offenders = {
        path.name: found
        for path in ENVIRONMENT_FILES
        if (
            found := sorted(
                name
                for name in names
                if _sets_env(path.read_text(), name, template=path == ENV_TEMPLATE)
            )
        )
    }
    assert not offenders, (
        f"variables dlt would read as source or resource arguments: {offenders}\n"
        "Give the variable a name no dlt argument has (the release uses an _INPUT suffix)."
    )


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
    declared dependency.
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

    So a composite action's exactly-pinned `uses:` — `astral-sh/setup-uv` in
    `.github/actions/setup` — is unwatched unless its directory is listed:
    still pinned, frozen, and nothing goes red.

    `setup-uv` is the one that makes it matter: it stopped publishing moving
    major tags at v8, so a Dependabot PR is the only thing that can bump it
    (see the `dependency-versions` skill).
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


JUSTFILE = REPO_ROOT / "justfile"

# Derived from what a recipe does, not a list of recipe names, so a new
# writing recipe is covered without anyone remembering to add it.
WRITING_COMMANDS = (
    "python -m ingest.pipeline",
    "python -m lake.lakehouse --expire",
    "python -m transform.",
    "python -m publish.restore_history",
    "dbt build",
    "dagster job execute",
    "dagster asset materialize",
)

# `name:`, `name dep:` or `name arg='': dep` at column 0. The negative lookahead
# excludes `:=` assignments like `export LAKEHOUSE_DIR := …`.
_RECIPE_HEADER = re.compile(r"^(?P<name>[a-z][a-z0-9-]*)(?P<params>[^:\n]*):(?!=)(?P<deps>.*)$")


def just_recipes() -> dict[str, tuple[str, str]]:
    """recipe name -> (its dependency list, its body)."""
    recipes: dict[str, tuple[str, str]] = {}
    name: str | None = None
    deps = ""
    body: list[str] = []
    for line in JUSTFILE.read_text().splitlines():
        header = _RECIPE_HEADER.match(line)
        if header:
            if name is not None:
                recipes[name] = (deps, "\n".join(body))
            name, deps, body = header["name"], header["deps"], []
        elif name is not None and (line.startswith((" ", "\t")) or not line.strip()):
            body.append(line)
        elif line.startswith("#"):
            continue
        else:
            if name is not None:
                recipes[name] = (deps, "\n".join(body))
            name, deps, body = None, "", []
    if name is not None:
        recipes[name] = (deps, "\n".join(body))
    return recipes


def writing_recipes() -> dict[str, tuple[str, str]]:
    return {
        name: parts
        for name, parts in just_recipes().items()
        if any(command in parts[1] for command in WRITING_COMMANDS)
    }


def test_the_justfile_scan_finds_the_recipes_it_thinks_it_does():
    """Vacuity guard, and it comes first.

    The test below iterates over whatever this scan returns, so a regex that
    matched nothing — or that swallowed `export LAKEHOUSE_DIR := …` as a recipe
    — would pass it by having nothing to check.
    """
    recipes = just_recipes()
    assert "where" in recipes, "the announcing recipe itself is missing"
    assert "export" not in recipes, "`:=` assignments are being read as recipes"
    writing = writing_recipes()
    assert {"ingest", "dbt-build", "materialize", "test-pipeline"} <= set(writing), (
        f"the writing-recipe scan found only {sorted(writing)}"
    )


def test_every_recipe_that_writes_says_where_it_is_writing():
    """dbt's only "where am I" line names the *target*, and there is one target
    here on purpose (`dbt/profiles.yml` argues it), so that line is a constant.
    What varies is the file, and no dbt output prints it.

    So a recipe that writes either depends on `where`, or exports its own
    `WAREHOUSE_PATH` and announces that itself — `test-pipeline` and the two
    course recipes do the latter, and taking `where` as a dependency there would
    print the *outer* value, which is worse than printing nothing.
    """
    silent = []
    for name, (deps, body) in sorted(writing_recipes().items()):
        if "where" in deps.split():
            continue
        if "export WAREHOUSE_PATH" in body:
            continue
        silent.append(name)
    assert not silent, (
        f"these recipes write to the warehouse or the landing zone without saying "
        f"where: {silent}. Add `where` as their first dependency, or export "
        f"WAREHOUSE_PATH in the body and echo it as `test-pipeline` does."
    )


def test_both_ways_of_announcing_are_actually_in_use():
    """The test above passes a recipe on either of two conditions, so a tree
    where every writing recipe took the same route would leave the other branch
    unexercised and free to rot."""
    by_dependency, by_own_export = [], []
    for name, (deps, body) in writing_recipes().items():
        (by_dependency if "where" in deps.split() else by_own_export).append(name)
    assert by_dependency, "no recipe depends on `where`"
    assert by_own_export, "no recipe exports its own WAREHOUSE_PATH any more"


def test_every_recipe_that_writes_refuses_a_dbt_dotenv():
    """dbt 1.12 loads `dbt/.env` for any variable a recipe leaves unset, so a
    writing recipe must reach `_no-dbt-dotenv` — through `where`, which depends
    on it, or directly. The recipes that export their own WAREHOUSE_PATH skip
    `where` and repeat the dependency by hand, and the announcing test above
    passes a new one without it."""
    guard = "_no-dbt-dotenv"
    assert guard in just_recipes()["where"][0].split(), (
        f"`where` no longer depends on `{guard}`, so no recipe that depends on "
        f"`where` is guarded against dbt/.env"
    )
    unguarded = sorted(
        name
        for name, (deps, _) in writing_recipes().items()
        if not {"where", guard} & set(deps.split())
    )
    assert not unguarded, (
        f"these recipes write without refusing a dbt/.env: {unguarded}. Add "
        f"`where` as their first dependency or, if they export WAREHOUSE_PATH, "
        f"`{guard}`."
    )


def _recipe(name: str) -> str:
    """The body of one `just` recipe, from its header to the next blank-line gap.

    Read as text rather than by running `just --evaluate`: the assertions here
    are about what the recipe *says*, and a recipe that has stopped exporting
    something evaluates perfectly well.
    """
    lines = (REPO_ROOT / "justfile").read_text().splitlines()
    # `name:` or `name param:` — `course-query sql:` takes an argument.
    start = next(i for i, line in enumerate(lines) if line.startswith((f"{name}:", f"{name} ")))
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line and not line.startswith((" ", "\t", "#")):
            break
        body.append(line)
    return "\n".join(body)


def test_the_fixture_pipeline_isolates_every_piece_of_state_it_touches():
    """`just test-pipeline` must not leave anything behind for the next real run.

    Four overrides, each for a leak that was observed:

    * `WAREHOUSE_PATH` — without it a fixture run overwrites the real warehouse
      with the 17-country slice.
    * `LAKEHOUSE_DIR` — dlt *lands* in the lakehouse, so without it the slice
      merges into a landing zone holding a weather archive no rebuild affords.
    * `DBT_RUN_RESULTS_PATH` (with `--target-path`) — dbt writes its artifacts
      to `dbt/target/` wherever the build pointed, and `analytics.pipeline_runs`
      records whatever that file last held, so the next `just pipeline-status`
      would file the fixture timings in the real warehouse's build history.
    * `DBT_MANIFEST_PATH` — the same directory, so the test inventory reads the
      fixture build's manifest rather than a real one alongside it.

    And two more, each set only when the landing zone has left the disk, and
    each the second leak again in another place:

    * `LAKEHOUSE_DATA_PATH` outranks `LAKEHOUSE_DIR`, so without its own
      override the slice's Parquet lands under the real prefix.
    * `LAKEHOUSE_METADATA_SCHEMA` is what separates two lakehouses inside one
      Postgres database, and `LAKEHOUSE_DIR` does not separate them at all — so
      without it the fixture run rewrites the real catalog's tables. It is the
      metadata *schema* and not `LAKEHOUSE_CATALOG` on purpose: the fixture run
      keeps the same database, because a throwaway one would have to be created.

    A conditional `export X=` satisfies this, as `LAKEHOUSE_DATA_PATH`'s already
    does — the recipe only overrides what the environment actually set.

    Asserted as a set rather than by reading the recipe's behaviour, because
    each is invisible when missing: the fixture run still passes, and what
    breaks is the *next* command against real data.
    """
    recipe = _recipe("test-pipeline")
    for variable in (
        "WAREHOUSE_PATH",
        "LAKEHOUSE_DIR",
        "DBT_RUN_RESULTS_PATH",
        "DBT_MANIFEST_PATH",
        "LAKEHOUSE_DATA_PATH",
        "LAKEHOUSE_METADATA_SCHEMA",
    ):
        assert f"export {variable}=" in recipe, (
            f"`just test-pipeline` no longer overrides {variable}, so a fixture run "
            f"leaks that state into the next command that reads it"
        )
    assert '--target-path "$DBT_TARGET_PATH"' in recipe, (
        "the env var alone is not enough: dbt must be told to write there too, "
        "or the override points at a file the build never creates"
    )


# What each course-recipe command reads or writes, so what to point at the
# sandbox; `dbt deps` touches none of it. `\s+` allows a `\` continuation's gap.
_DBT_COMMAND = r"\buv\s+run\s+dbt\s+(?!deps\b)\w+"
_COURSE_STATE_BY_COMMAND = {
    _DBT_COMMAND: ("WAREHOUSE_PATH", "LAKEHOUSE_DIR", "DBT_TARGET_PATH"),
    r"-m ingest\.pipeline\b": ("LAKEHOUSE_DIR",),
    r"-m transform\.pipeline_status\b": (
        "WAREHOUSE_PATH",
        "LAKEHOUSE_DIR",
        "DBT_MANIFEST_PATH",
        "DBT_RUN_RESULTS_PATH",
    ),
    r"-m transform\.(?!pipeline_status\b)\w+": ("WAREHOUSE_PATH",),
    r"\blake\.lakehouse\b": ("LAKEHOUSE_DIR",),
}


def test_every_course_recipe_keeps_the_sandbox_to_itself():
    """A `course-*` recipe must point everything it runs at `data/course/`.

    Exporting `WAREHOUSE_PATH` alone is not enough, as measured when a
    `course-rebuild` did exactly that: dbt attached the
    *real* landing zone, and every staging model is a view over it, so a drill
    rebuilt a sandbox copy's `fct_emissions_energy` from 4,096 rows to 43,138 with
    a green build. The same recipe, and `course-sandbox`, wrote dbt's artifacts
    to `dbt/target/`, and a `just pipeline-status` against a copy of the real
    warehouse then filed the sandbox build's 571 nodes as a seventh invocation
    in `analytics.pipeline_runs`, a table every release carries.

    Required variables are derived from the commands each recipe runs rather
    than listed per recipe, so a new course recipe, or a command added to an
    existing one, is held without editing this test. Each value must land under
    `data/course/`, because exporting the real path is the same leak spelled out.
    """
    justfile = (REPO_ROOT / "justfile").read_text()
    names = re.findall(r"^(course-[\w-]+)[^:\n]*:", justfile, re.MULTILINE)
    assert {"course-sandbox", "course-rebuild", "course-transform", "course-query"} <= set(names)

    fired: set[str] = set()
    for name in names:
        recipe = _recipe(name)
        code = "\n".join(line for line in recipe.splitlines() if not line.strip().startswith("#"))
        # Read shell commands, not lines: a `\` continuation is one command.
        code = re.sub(r"\\\n\s*", " ", code)
        exports = dict(re.findall(r'^\s*export (\w+)="([^"]*)"', code, re.MULTILINE))
        target = exports.get("DBT_TARGET_PATH", "$DBT_TARGET_PATH")

        for pattern, variables in _COURSE_STATE_BY_COMMAND.items():
            if not re.search(pattern, code):
                continue
            fired.add(pattern)
            for variable in variables:
                value = exports.get(variable, "").replace("$DBT_TARGET_PATH", target)
                assert "/data/course/" in value, (
                    f"`just {name}` runs something matching {pattern!r} without pointing "
                    f"{variable} into data/course/ (it is {value or 'unset'!r}), so it "
                    f"reads or writes the real one"
                )
            if "LAKEHOUSE_DIR" in variables:
                # Read from lakehouse.REMOTE_ENV_VARS, not listed here: one added
                # there and missed here is a sandbox writing into the real zone.
                for remote in lakehouse.REMOTE_ENV_VARS:
                    # Matched as a word on the unset line: `unset A B` clears both.
                    assert re.search(rf"^\s*unset\b[^\n#]*\b{remote}\b", code, re.MULTILINE), (
                        f"`just {name}` sets the course LAKEHOUSE_DIR but leaves "
                        f"{remote}, which outranks it and names the real landing zone"
                    )

        # As in test-pipeline, the variable alone is not enough — every dbt build
        # command needs --target-path. Split on any command separator, not just `&`.
        for command in re.split(r"&&|\|\||[;|&\n]", code):
            if not re.search(_DBT_COMMAND, command):
                continue
            assert '--target-path "$DBT_TARGET_PATH"' in command, (
                f"`just {name}` runs `{command.strip()}` without --target-path, so "
                f"its artifacts land in dbt/target/"
            )

    assert fired == set(_COURSE_STATE_BY_COMMAND), (
        f"no course recipe runs {sorted(set(_COURSE_STATE_BY_COMMAND) - fired)} any more, "
        f"so that rule measures nothing — update the table"
    )
