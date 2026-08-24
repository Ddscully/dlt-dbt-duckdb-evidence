"""Counts cited in prose must match what dbt actually builds.

Nothing else in the project checks this, and the gap is not theoretical. The
route fix in `fct_cbam_exposure` added one data test, and the number moved from
368 to 369 in `CLAUDE.md` and `docs/DATA_QUALITY.md` and nowhere else — leaving
`README.md` describing `docs/DATA_QUALITY.md` as "the 368 dbt tests" while
linking to a file whose first line said 369. Fourteen sites were stale. In the
same review, "the model's 22 data tests" turned out to be wrong in thirteen
places: `stg_retail_lines` has 19 attached data tests, and the 22 was
`dbt build`'s node total, which counts the model itself.

`just lint`, `pytest` and `dbt build` were all green throughout. A derived total
written into prose is an untested assertion, and this file is the test.

**What this does not check.** The allowed set is global, so a per-model count
(19, 20) would satisfy a sentence making a project-wide claim, and vice versa.
Anchoring each citation to its own site would catch that and would drift on
every reflow; the cheap version catches the whole class that has actually
broken here — a number that is no longer any of the true ones.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from orchestration.resources import dbt_project

# Same reason as `tests/test_definitions.py`: `just test` runs before
# `dbt deps && dbt parse` in ci.yml, so the manifest is not there yet. CI
# re-runs this file after the parse step.
pytestmark = pytest.mark.skipif(
    not dbt_project.manifest_path.exists(),
    reason="needs dbt/target/manifest.json — run `just dbt-deps` and `dbt parse` first",
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# Every integer sitting immediately in front of a test-noun. Deliberately narrow:
# the legitimate neighbouring figures all precede a *different* noun — "291 of
# the 369", "391 audit tables", "22 orphans", "367 of the 369 tests" — so they
# are never captured and never need exempting.
# CLAUDE.md writes some counts as words ("There are eighteen unit tests"), so
# the pattern reads both — `fct_retail_returns` shipped "all eleven data tests"
# for a model with ten, and a digits-only scanner passed it twice.
#
# Only ten and above. Below that the words are always local ("Two unit tests
# catch all five", "four tests", "one test"), never a project-wide or per-model
# total, and including them produced nine false positives against zero finds.
WORDS = {
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}
CLAIM = re.compile(
    rf"\b(\d+|{'|'.join(WORDS)})\s+(?:dbt\s+|data\s+|unit\s+)?tests?\b", re.IGNORECASE
)


def as_int(token: str) -> int:
    return int(token) if token.isdigit() else WORDS[token.lower()]


# Prose that makes these claims. `git ls-files` rather than a glob, so the
# gitignored `docs/sessions/` transcripts (which quote historical counts by
# design) are outside this by construction rather than by an exclude list.
SCANNED = (
    "*.md",
    "dbt/models/staging/_unit_tests.yml",
    "dbt/models/marts/_unit_tests.yml",
)


def tracked_prose() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", *SCANNED],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return [REPO_ROOT / p for p in out]


def manifest() -> dict:
    return json.loads(dbt_project.manifest_path.read_text())


def data_tests(man: dict) -> list[dict]:
    return [v for v in man["nodes"].values() if v.get("resource_type") == "test"]


def attached_to(man: dict, model: str) -> int:
    """Data tests dbt attaches to one model.

    Not the same as `dbt build --select <model>` reports: that total includes the
    model node itself and any test pulled in by eager indirect selection, which
    is exactly how "22" got written down for a model with 19.
    """
    return sum(1 for v in data_tests(man) if (v.get("attached_node") or "").endswith(f".{model}"))


def expected_counts(man: dict) -> set[int]:
    """Every test count this project's prose is allowed to state.

    Derived, never written down. A literal here would be the same untested
    assertion one layer further in, and the point of the file is that a count
    nobody re-measures goes stale — so a model that gains a test moves its own
    citations' expected value with it.

    The two per-model entries widen the set and the cost is real: with 19 in it,
    nothing can catch a sentence claiming "19 dbt tests" of the whole project.
    They earn it because the error that prompted this file *was* a per-model
    count, wrong in thirteen places, and leaving them out would put the class
    that actually broke outside the guard. Only models whose count is genuinely
    cited belong — an entry for a model no prose mentions widens the set for
    nothing.
    """
    data = len(data_tests(man))
    unit = len(man.get("unit_tests", {}))
    return {
        data,  # project-wide data tests
        unit,  # project-wide unit tests
        data + unit,  # the "N tests alongside the models" figure
        attached_to(man, "stg_retail_lines"),  # cited 3x
        attached_to(man, "fct_cbam_exposure"),  # cited 4x
        attached_to(man, "fct_fx_rates_daily"),  # cited 3x
        attached_to(man, "fct_fx_rates_periods"),  # cited 4x
        attached_to(man, "fct_retail_returns"),  # cited 2x
    }


def test_every_documented_test_count_is_one_dbt_actually_builds():
    man = manifest()
    allowed = expected_counts(man)
    stale: list[str] = []
    seen = 0
    for path in tracked_prose():
        # Scanned whole-file, not line by line, because these docs are hard
        # wrapped at ~80 characters and the claims straddle the wraps: the one
        # in `docs/DATA_QUALITY.md` puts "10 unit" at the end of a line and
        # "tests." at the start of the next. A per-line scan is blind to
        # precisely those and passed a mutated unit-test count.
        text = path.read_text()
        for match in CLAIM.finditer(text):
            seen += 1
            if as_int(match.group(1)) not in allowed:
                line = text.count("\n", 0, match.start()) + 1
                rel = path.relative_to(REPO_ROOT)
                claim = " ".join(match.group(0).split())
                stale.append(f"  {rel}:{line}: {claim!r} — allowed: {sorted(allowed)}")
    assert seen > 25, (
        f"scanner matched only {seen} claims; the patterns or the file list have drifted"
    )
    assert not stale, "test counts in prose disagree with the dbt manifest:\n" + "\n".join(stale)
