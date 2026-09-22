"""Counts of what dbt builds stay out of prose, and the few that remain are true.

A derived total written into prose is an assertion nothing else checks:
`just lint`, `pytest` and `dbt build` all stay green while a README cites last
month's test count, and every restatement is one more file to edit when a test
is added. So the prose says "every data test on the model", and three figures
are kept and checked against the manifest: the project's test totals (the
README headline), the description coverage `FOR_REVIEWERS.md` scores on, and
the course's `PASS=` verdicts. Any other count of tests, mart models,
additivity labels or contracted columns fails, with the phrasing to use instead.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from orchestration.resources import dbt_project

# ci.yml runs pytest before `dbt parse`, so the manifest is missing there; it
# re-runs this file after the parse, which `tests/test_workflows.py` enforces.
pytestmark = pytest.mark.skipif(
    not dbt_project.manifest_path.exists(),
    reason="needs dbt/target/manifest.json — run `just dbt-deps` and `dbt parse` first",
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# Every integer, or number word, immediately in front of a test-noun. Narrow on
# purpose: the neighbouring figures precede a different noun ("391 audit
# tables", "22 orphans"), so they are never captured and never need exempting.
#
# Number words run from ten to ninety-nine, generated rather than listed so no
# hyphenated compound ("twenty-nine") is missing. Below ten the words are always
# local ("two unit tests catch all five"), never a total, and reading them
# produced only false positives.
_TEENS = (
    "ten", "eleven", "twelve", "thirteen", "fourteen",
    "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
)  # fmt: skip
_TENS = ("twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
_ONES = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine")

WORDS = {word: 10 + i for i, word in enumerate(_TEENS)}
for _t, _tens in enumerate(_TENS, start=2):
    WORDS[_tens] = _t * 10
    for _o, _one in enumerate(_ONES, start=1):
        WORDS[f"{_tens}-{_one}"] = _t * 10 + _o
# `of those` / `of the` may sit between number and noun ("Eighteen of those tests
# are dbt unit tests"). Nothing longer: the further the noun drifts, the more the
# pattern matches arithmetic ("367 of the 369 tests" must capture 369, not 367).
_WORD_ALTERNATION = "|".join(sorted(WORDS, key=len, reverse=True))
CLAIM = re.compile(
    rf"\b(\d+|{_WORD_ALTERNATION})\s+(?:of\s+(?:those|the|them|its)\s+)?"
    rf"(?:dbt\s+|data\s+|unit\s+)?tests?\b",
    re.IGNORECASE,
)


def as_int(token: str) -> int:
    return int(token) if token.isdigit() else WORDS[token.lower()]


# Prose that makes these claims. `git ls-files` rather than a glob, so the
# gitignored docs/sessions/ transcripts (which quote old counts by design) are
# out by construction. The cost: a doc never `git add`ed is not scanned, so
# stage new prose before trusting a green run.
#
# The yml pathspec is a wildcard so a new `_*.yml` is scanned without anyone
# remembering this list. Only test, mart and contract phrasings are read there; the row
# counts, shares and distinct values in the column descriptions stay unguarded,
# because checking them needs a warehouse with the full data, which CI lacks.
SCANNED = (
    "*.md",
    "dbt/models/**/_*.yml",
)


# Label counts have been written into two modules' docstrings, so that scan reads
# those as well as the markdown.
ADDITIVITY_PROSE = (
    "*.md",
    "tests/test_additivity.py",
    "publish/export_warehouse.py",
)


def tracked_prose(patterns: tuple[str, ...] = SCANNED) -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", *patterns],
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


def project_counts(man: dict) -> set[int]:
    """The totals a sentence may state: data tests, unit tests, and both."""
    data = len(data_tests(man))
    unit = len(man.get("unit_tests", {}))
    return {data, unit, data + unit}


def test_every_documented_test_count_is_one_dbt_actually_builds():
    project = project_counts(manifest())
    stale: list[str] = []
    seen = 0
    for path in tracked_prose():
        # Whole-file, not per line: the docs are hard-wrapped and claims
        # straddle the wraps ("10 unit" ending one line, "tests." starting the
        # next), which a per-line scan cannot see.
        text = path.read_text()
        for match in CLAIM.finditer(text):
            seen += 1
            if as_int(match.group(1)) in project:
                continue
            line = text.count("\n", 0, match.start()) + 1
            claim = " ".join(match.group(0).split())
            stale.append(f"  {path.relative_to(REPO_ROOT)}:{line}: {claim!r}")
    assert seen, (
        "the scanner matched no test count at all, and the README headline states "
        "one: the pattern or the file list has drifted"
    )
    assert not stale, (
        f"test counts in prose that are not a project total {sorted(project)}:\n"
        + "\n".join(stale)
        + "\nA count of one model's tests moves with every test added to it: write "
        '"every data test on the model". Keep project totals to the README headline.'
    )


# Every integer counting marts. Two things `CLAIM` does not need:
#
# * A lookbehind, because row counts carry thousands separators: `\b(\d+)` would
#   read the "787" inside "808,787".
# * The head noun, because "mart" is usually a modifier here: "808,787 mart rows"
#   counts rows, "19 mart relations" counts marts. Plural `marts` is unambiguous;
#   singular `mart` counts only before relation/model/node/table.
#
# No number words: mart counts are written in digits.
MART_CLAIM = re.compile(
    r"(?<![\d,])(\d+)\s+(?:of\s+(?:those|the|them)\s+)?"
    r"(?:marts\b|mart\s+(?:relation|model|node|table)s?\b)",
    re.IGNORECASE,
)


def test_no_prose_counts_the_mart_models():
    """A mart-model count moves with every model added, and no build prints it."""
    stale = []
    for path in tracked_prose():
        text = path.read_text()
        for match in MART_CLAIM.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            claim = " ".join(match.group(0).split())
            stale.append(f"  {path.relative_to(REPO_ROOT)}:{line}: {claim!r}")
    assert not stale, (
        'prose counts the mart models; write "every mart model" instead:\n' + "\n".join(stale)
    )


# Every integer in front of an additivity label, the release map's shape ("N
# columns across M relations") and the literal yml entry count. Not scanned: a
# bare "N labels", which docs/PRACTICES.md also uses for the retail country map.
ADDITIVITY_LABEL = re.compile(
    r"(?<![\d,])(\d+)(?:\s+of\s+(?:the\s+)?(\d+))?"
    # Bounded, and word/space/dash only: it must reach across "of the N numeric
    # mart columns are" but never across the `"): "` that separates a digit from
    # a label inside `EXTRA_ADDITIVITY`, or every line of that dict is a claim.
    r"[\w\s—–-]{0,40}?`?"
    r"(semi[-_]additive|non[-_]additive|not[-_]a[-_]measure|additive|EXTRA_ADDITIVITY)\b",
    re.IGNORECASE,
)
ADDITIVITY_MAP = re.compile(
    r"(?<![\d,])(\d+)\s+columns?\s+across\s+(\d+)\s+relations?\b", re.IGNORECASE
)
ADDITIVITY_YML = re.compile(
    r"(?<![\d,])(\d+)\s+literal\s+`?additivity:?`?\s+(?:entries|lines)\b", re.IGNORECASE
)


def test_no_prose_counts_the_additivity_labels():
    """Every numeric column added moves every label count. The two modules in
    `ADDITIVITY_PROSE` are scanned too: a docstring is prose."""
    stale: list[str] = []
    for path in tracked_prose(ADDITIVITY_PROSE):
        text = path.read_text()
        for pattern in (ADDITIVITY_LABEL, ADDITIVITY_MAP, ADDITIVITY_YML):
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                claim = " ".join(match.group(0).split())
                stale.append(f"  {path.relative_to(REPO_ROOT)}:{line}: {claim!r}")
    assert not stale, (
        'prose counts additivity labels; write a share ("about half are non-additive") '
        "or no figure:\n" + "\n".join(stale)
    )


# The contract's own phrasings, which carry neither a test nor a mart noun.
CONTRACT_CLAIMS = (
    re.compile(r"(\d+)\s+columns,\s+each\s+with\s+a\s+`data_type`"),
    re.compile(r"(\d+)\s+columns\s+with\s+a\s+declared\s+type"),
    re.compile(r"(\d+)\s+relations\s+\((\d+)\s+models"),
)


def test_no_prose_counts_the_contract():
    stale: list[str] = []
    for path in tracked_prose():
        text = path.read_text()
        for pattern in CONTRACT_CLAIMS:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                claim = " ".join(match.group(0).split())
                stale.append(f"  {path.relative_to(REPO_ROOT)}:{line}: {claim!r}")
    assert not stale, (
        'prose counts the contract; write "every mart model" or "every column" instead:\n'
        + "\n".join(stale)
    )


def test_the_documented_description_coverage_is_what_the_ymls_carry():
    """The figure `FOR_REVIEWERS.md` §6 scores metadata completeness on.

    All three numbers are recomputed, the percentage too: a stale numerator
    with a fresh denominator would still round to something plausible.
    """
    contracted = [
        v
        for v in manifest()["nodes"].values()
        if v.get("resource_type") == "model" and (v["config"].get("contract") or {}).get("enforced")
    ]
    cols = [c for v in contracted for c in v.get("columns", {}).values()]
    described = sum(1 for c in cols if c.get("description"))
    pattern = re.compile(r"(\d+)\s+of\s+those\s+(\d+)\s+columns\s+\((\d+)%\)")
    seen = 0
    for path in tracked_prose():
        text = path.read_text()
        for match in pattern.finditer(text):
            seen += 1
            got = tuple(int(g) for g in match.groups())
            expected = (described, len(cols), round(described / len(cols) * 100))
            assert got == expected, (
                f"{path.relative_to(REPO_ROOT)} claims {got}, the ymls carry {expected}"
            )
    assert seen == 1, f"expected the coverage claim in exactly one place, found {seen}"


# `dbt build`'s verdict as the course quotes it back: `PASS=561 WARN=0 …`.
_PASS = re.compile(r"\bPASS=(\d+)")

# Which documents mean a *whole* build by it. A `PASS=` is only comparable to a
# project total when the build it describes was a project build, and plenty here
# are not: `unit-testing-dbt-models` and the `_unit_tests.yml` files quote
# `PASS=83`, `PASS=22` and `PASS=16` from `dbt build --select <model>`, which are
# right.
#
# Derived from the text rather than listed: a document that cites a `course-*`
# recipe is describing the sandbox build, which is every node. That happens to
# select the course and its authoring skill today, and it will keep selecting the
# right documents without anyone maintaining a list — which a hand-written scope
# would not, since the whole failure mode here is prose nobody revisits.
_WHOLE_BUILD = re.compile(r"just course-(sandbox|rebuild)")

# Resource types `dbt build` executes and reports in PASS. Exposures are
# resolved, not built, and land in dbt's NO-OP bucket instead.
BUILT_RESOURCE_TYPES = ("model", "seed", "snapshot", "test")


def built_nodes(man: dict) -> int:
    """What a green `dbt build` prints as `PASS=`.

    Unit tests live under their own manifest key rather than in `nodes`, so they
    are counted separately; miss them and this reads 36 low, which is exactly
    the size of a plausible-looking wrong answer.
    """
    return sum(
        1 for v in man["nodes"].values() if v.get("resource_type") in BUILT_RESOURCE_TYPES
    ) + len(man.get("unit_tests", {}))


def test_every_quoted_build_verdict_is_the_one_dbt_would_print():
    """`PASS=n` in prose must be the number of nodes `dbt build` runs.

    The course quotes this literal twelve times, and load-bearingly: each drill
    shows the *same* verdict before and after seeding a bug, so the whole claim
    of the module is that this number does not move. It sat at 402 while the
    suite grew to 561 — invisible to `CLAIM` above, which needs a test noun
    after the number and finds `WARN=0` instead.

    Derived rather than measured, so it needs no warehouse: a build's PASS count
    is a property of the manifest, and it is the same 561 in CI's 17-country
    slice as on a full warehouse, because a fixture changes rows and never nodes.
    """
    expected = built_nodes(manifest())
    stale: list[str] = []
    seen = 0
    for path in tracked_prose():
        text = path.read_text()
        if not _WHOLE_BUILD.search(text):
            continue
        for line, row in enumerate(text.splitlines(), start=1):
            for match in _PASS.finditer(row):
                seen += 1
                if int(match.group(1)) != expected:
                    rel = path.relative_to(REPO_ROOT)
                    stale.append(f"  {rel}:{line}: PASS={match.group(1)}, dbt builds {expected}")
    assert not stale, "quoted build verdicts disagree with the manifest:\n" + "\n".join(stale)
    assert seen, (
        "no `PASS=n` found in prose about a whole build — the course quotes it in "
        "every drill, so one of `_PASS` and `_WHOLE_BUILD` has stopped matching "
        "and this check is now looking at nothing"
    )
