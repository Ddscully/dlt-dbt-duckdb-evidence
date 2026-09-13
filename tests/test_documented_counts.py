"""Counts cited in prose must match what dbt actually builds.

A derived total written into prose is an assertion nothing else checks:
`just lint`, `pytest` and `dbt build` all stay green while a README cites last
month's test count. Each scan here finds a number in front of a counted noun in
tracked prose and checks it against the manifest.

**What this does not check.** A project-wide total is accepted in any sentence,
including one about a single model, and any of the project totals satisfies any
project-wide sentence. Anchoring each citation to its own site would close that
and drift on every reflow; this catches the class that has actually broken — a
number that is no longer any of the true ones.
"""

from __future__ import annotations

import collections
import json
import re
import subprocess
from pathlib import Path

import pytest

from orchestration.resources import dbt_project
from publish.export_warehouse import additivity as published_additivity

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
# remembering this list. Only test and mart nouns are read there; the row
# counts, shares and distinct values in the column descriptions stay unguarded,
# because checking them needs a warehouse with the full data, which CI lacks.
SCANNED = (
    "*.md",
    "dbt/models/**/_*.yml",
)


# The additivity figures are also written into two modules' docstrings, so that
# scan reads those as well as the markdown.
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


def attached_to(man: dict, model: str) -> int:
    """Data tests dbt attaches to one model.

    Not what `dbt build --select <model>` reports: that total also counts the
    model node and any test pulled in by eager indirect selection.
    """
    return sum(1 for v in data_tests(man) if (v.get("attached_node") or "").endswith(f".{model}"))


# Models whose per-model test count is quoted in prose. A count here is only
# accepted near a mention of its own model, so adding one does not widen what a
# project-wide sentence may claim.
CITED_MODELS = (
    "dim_date",
    "stg_retail_lines",
    "fct_cbam_exposure",
    "fct_fx_rates_daily",
    "fct_fx_rates_periods",
    "fct_retail_returns",
    "fct_retail_customer_cohorts",
    "dim_retail_customer",
    "stg_wdi",
    # Prose about these two names each model beside its own count rather than
    # their sum: accepting sums over pairs would make every such sum legal.
    "stg_weather_daily",
    "fct_country_weather_year",
)


def owning_model(text: str, pos: int) -> str | None:
    """Which model the prose at `pos` is talking about.

    The nearest *preceding* mention, unbounded, because that is how these
    documents establish context: a heading names the model and everything under
    it is about that model until the next one. A fixed-width window round the
    number does not work — `compliance-models/SKILL.md` writes "This model's …
    data tests" with `fct_cbam_exposure` several paragraphs up.
    """
    before = text[:pos]
    best, best_at = None, -1
    for model in CITED_MODELS:
        at = before.rfind(model)
        if at > best_at:
            best, best_at = model, at
    return best if best_at >= 0 else None


def project_counts(man: dict) -> set[int]:
    """Counts a sentence may state about the project as a whole."""
    data = len(data_tests(man))
    unit = len(man.get("unit_tests", {}))
    return {data, unit, data + unit}


def model_counts(man: dict) -> dict[str, int]:
    """Per-model counts, each admissible only near its own model's name.

    In the project-wide set, any model's count would be legal in every sentence,
    and a per-model count can equal a recent project total — the exact staleness
    this file exists to catch. Scoped, the list can grow without each addition
    weakening every other check.
    """
    return {m: attached_to(man, m) for m in CITED_MODELS}


def expected_counts(man: dict) -> set[int]:
    """Everything that is a true count somewhere. Used only for reporting."""
    return project_counts(man) | set(model_counts(man).values())


def test_every_documented_test_count_is_one_dbt_actually_builds():
    man = manifest()
    project = project_counts(man)
    per_model = model_counts(man)
    stale: list[str] = []
    seen = 0
    for path in tracked_prose():
        # Whole-file, not per line: the docs are hard-wrapped and claims
        # straddle the wraps ("10 unit" ending one line, "tests." starting the
        # next), which a per-line scan cannot see.
        text = path.read_text()
        for match in CLAIM.finditer(text):
            seen += 1
            value = as_int(match.group(1))
            if value in project:
                continue
            owner = owning_model(text, match.start())
            if owner is not None and per_model[owner] == value:
                continue
            line = text.count("\n", 0, match.start()) + 1
            rel = path.relative_to(REPO_ROOT)
            claim = " ".join(match.group(0).split())
            owners = sorted(m for m, n in per_model.items() if n == value)
            hint = f" (reads as {owner or 'no model'}; {value} is {'/'.join(owners) or 'no model'})"
            stale.append(f"  {rel}:{line}: {claim!r}{hint} — project-wide: {sorted(project)}")
    assert seen > 35, (
        f"scanner matched only {seen} claims; the patterns or the file list have drifted"
    )
    assert not stale, "test counts in prose disagree with the dbt manifest:\n" + "\n".join(stale)


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


def mart_counts(man: dict) -> set[int]:
    """Counts a sentence may state about the marts layer.

    Both the node total and the distinct-model total, because a versioned model
    is two nodes and one model and prose legitimately means either. The set is
    deliberately not narrowed further: which of the two a sentence means is a
    judgement, and the failure this catches is a number that is *neither*.
    """
    marts = [
        v
        for v in man["nodes"].values()
        if v.get("resource_type") == "model" and v["config"].get("schema") == "marts"
    ]
    return {len(marts), len({v["name"] for v in marts})}


def test_every_documented_mart_count_is_one_dbt_actually_builds():
    """The test-count check, one noun over: a mart count is not a number any
    build prints, so nothing else can disagree with it.

    No floor on `seen`, unlike the test-count scan: mart counts appear in a
    handful of places, so a floor would be a number to maintain, not a guard.
    """
    allowed = mart_counts(manifest())
    stale = []
    for path in tracked_prose():
        text = path.read_text()
        for match in MART_CLAIM.finditer(text):
            if int(match.group(1)) in allowed:
                continue
            line = text.count("\n", 0, match.start()) + 1
            claim = " ".join(match.group(0).split())
            stale.append(f"  {path.relative_to(REPO_ROOT)}:{line}: {claim!r}")
    assert not stale, (
        f"mart counts in prose disagree with the dbt manifest (it builds {sorted(allowed)}):\n"
        + "\n".join(stale)
    )


# Every integer in front of an additivity label, the release map's shape ("N
# columns across M relations") and the literal yml entry count.
#
# Its own scanner rather than a wider `CLAIM` because there are two bases: the
# marts ymls' literal `additivity:` entries and the manifest's labelled columns
# differ, since `fct_emissions_energy_v1` inherits its labels through
# `include: all`. A set holding both would make either legal anywhere, so each
# figure is bound to the noun it is written in front of.
#
# Not scanned: a bare "N labels", which docs/PRACTICES.md also uses for the
# retail country map.
ADDITIVITY_LABEL = re.compile(
    r"(?<![\d,])(\d+)(?:\s+of\s+(?:the\s+)?(\d+))?"
    # Bounded, and word/space/dash only: it must reach across "of the N numeric
    # mart columns are" but never across the `"): "` that separates a digit from
    # a label inside `EXTRA_ADDITIVITY`, or every line of that dict is a claim.
    r"[\w\s—–-]{0,40}?`?"
    r"(semi[-_]additive|non[-_]additive|not[-_]a[-_]measure|additive|EXTRA_ADDITIVITY)\b",
    re.IGNORECASE,
)
# A number in front of the whole vocabulary ("N published columns are labelled
# `additive` / `semi_additive` / …") is a total, not a count of `additive`. It is
# matched first and its span excluded from the per-label scan.
_VOCABULARY_SEPARATOR = r"`?\s*[/,]\s*`?"
_VOCABULARY = _VOCABULARY_SEPARATOR.join(
    ("additive", r"semi[-_]additive", r"non[-_]additive", r"not[-_]a[-_]measure")
)
ADDITIVITY_TOTAL = re.compile(
    rf"(?<![\d,])(\d+)[\w\s—–-]{{0,40}}?`?{_VOCABULARY}`?",
    re.IGNORECASE,
)
ADDITIVITY_MAP = re.compile(
    r"(?<![\d,])(\d+)\s+columns?\s+across\s+(\d+)\s+relations?\b", re.IGNORECASE
)
ADDITIVITY_YML = re.compile(
    r"(?<![\d,])(\d+)\s+literal\s+`?additivity:?`?\s+(?:entries|lines)\b", re.IGNORECASE
)

LABEL_VOCABULARY = ("additive", "semi_additive", "non_additive", "not_a_measure")


def yml_additivity_entries() -> int:
    """`additivity:` keys written by hand in the marts ymls.

    The "literal entries" basis prose quotes beside the manifest's. Counted as
    keys rather than parsed, because literal keys are what that prose claims.
    """
    return sum(
        len(re.findall(r"^\s*additivity:", path.read_text(), re.MULTILINE))
        for path in sorted((REPO_ROOT / "dbt/models/marts").rglob("_*.yml"))
    )


def additivity_counts(man: dict) -> tuple[dict[str, set[int]], set[int], set[int]]:
    """Allowed values, keyed by the noun each is written in front of.

    Returns `(per label, totals, relation counts)`. Both the marts figure and the
    published figure are admissible for each label: prose legitimately means
    either, and which one it means is a judgement. What it may not be is neither.
    """
    marts: collections.Counter[str] = collections.Counter()
    relations = set()
    for node in man["nodes"].values():
        if node.get("resource_type") != "model" or not node.get("path", "").startswith("marts/"):
            continue
        for spec in (node.get("columns") or {}).values():
            label = (spec.get("meta") or {}).get("additivity")
            if label:
                marts[label] += 1
                relations.add(f"{node['schema']}.{node.get('alias') or node['name']}")

    shipped = published_additivity() or {}
    published: collections.Counter[str] = collections.Counter()
    for columns in shipped.values():
        published.update(columns.values())

    per_label = {label: {marts[label], published[label]} for label in LABEL_VOCABULARY}
    # `EXTRA_ADDITIVITY` is the published set minus what dbt can describe.
    per_label["extra_additivity"] = {sum(published.values()) - sum(marts.values())}
    return per_label, {sum(marts.values()), sum(published.values())}, {len(relations), len(shipped)}


def test_every_documented_additivity_count_is_one_the_labels_actually_carry():
    """Additivity counts in prose must match the labels.

    `dbt build` is green whatever the prose says, `test_additivity.py` asserts
    coverage without reading the sentences that describe it, and the release
    manifest carries the real figure where nobody reads it.

    Bound per label rather than against one global set, so "16 are non-additive"
    fails even when 16 is the true count of another label.
    """
    per_label, totals, relation_counts = additivity_counts(manifest())
    yml_entries = yml_additivity_entries()
    stale: list[str] = []
    seen = 0

    def note(path: Path, text: str, match: re.Match, problem: str) -> None:
        line = text.count("\n", 0, match.start()) + 1
        claim = " ".join(match.group(0).split())
        stale.append(f"  {path.relative_to(REPO_ROOT)}:{line}: {claim!r} — {problem}")

    for path in tracked_prose(ADDITIVITY_PROSE):
        text = path.read_text()
        enumerations = []
        for match in ADDITIVITY_TOTAL.finditer(text):
            seen += 1
            enumerations.append(match.span())
            if int(match.group(1)) not in totals:
                note(path, text, match, f"{match.group(1)} is not a label total: {sorted(totals)}")
        for match in ADDITIVITY_LABEL.finditer(text):
            if any(start <= match.start() < end for start, end in enumerations):
                continue
            seen += 1
            value, total, label = match.group(1, 2, 3)
            key = label.lower().replace("-", "_")
            allowed = per_label[key]
            if int(value) not in allowed:
                note(path, text, match, f"{value} is not a {key} count: {sorted(allowed)}")
            if total is not None and int(total) not in totals:
                note(path, text, match, f"{total} is not a label total: {sorted(totals)}")
        for match in ADDITIVITY_MAP.finditer(text):
            seen += 1
            columns, rels = (int(g) for g in match.group(1, 2))
            if columns not in totals or rels not in relation_counts:
                note(
                    path,
                    text,
                    match,
                    f"the map is {sorted(totals)} columns over {sorted(relation_counts)} relations",
                )
        for match in ADDITIVITY_YML.finditer(text):
            seen += 1
            if int(match.group(1)) != yml_entries:
                note(path, text, match, f"the marts ymls carry {yml_entries} `additivity:` entries")

    assert seen >= 8, (
        f"scanner matched only {seen} additivity claims; the patterns or the file list have drifted"
    )
    assert not stale, "additivity counts in prose disagree with the labels:\n" + "\n".join(stale)


# The contract figures, which the scanners above cannot see: "407 columns, each
# with a `data_type`" and "21 relations (20 models…)" carry neither a test nor a
# mart noun. Anchored on the whole phrase rather than the noun, because
# "columns" is the most common counted thing in this prose and the additivity
# scan owns the other uses. These three phrasings are the ones that mean the
# contract.
CONTRACT_CLAIMS = (
    (re.compile(r"(\d+)\s+columns,\s+each\s+with\s+a\s+`data_type`"), "contracted columns"),
    (re.compile(r"(\d+)\s+columns\s+with\s+a\s+declared\s+type"), "contracted columns"),
    (re.compile(r"(\d+)\s+relations\s+\((\d+)\s+models"), "contracted relations and models"),
)


def contract_counts(man: dict) -> dict[str, set[int]]:
    """What dbt actually enforces: relations, distinct models, typed columns."""
    contracted = [
        v
        for v in man["nodes"].values()
        if v.get("resource_type") == "model" and (v["config"].get("contract") or {}).get("enforced")
    ]
    return {
        "contracted relations": {len(contracted)},
        "contracted models": {len({v["name"] for v in contracted})},
        "contracted columns": {sum(len(v.get("columns", {})) for v in contracted)},
    }


def test_every_documented_contract_count_is_one_dbt_actually_enforces():
    """The schema contract's own figures, held to the manifest."""
    counts = contract_counts(manifest())
    stale: list[str] = []
    for path in tracked_prose():
        text = path.read_text()
        for pattern, label in CONTRACT_CLAIMS:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                rel = path.relative_to(REPO_ROOT)
                claim = " ".join(match.group(0).split())
                if label == "contracted relations and models":
                    ok = (
                        int(match.group(1)) in counts["contracted relations"]
                        and int(match.group(2)) in counts["contracted models"]
                    )
                    truth = f"{sorted(counts['contracted relations'])} relations over {sorted(counts['contracted models'])} models"
                else:
                    ok = int(match.group(1)) in counts[label]
                    truth = f"{label} is {sorted(counts[label])}"
                if not ok:
                    stale.append(f"  {rel}:{line}: {claim!r} — {truth}")
    assert not stale, "contract counts in prose disagree with the manifest:\n" + "\n".join(stale)


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
