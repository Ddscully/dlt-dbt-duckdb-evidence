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

import collections
import json
import re
import subprocess
from pathlib import Path

import pytest

from orchestration.resources import dbt_project
from publish.export_warehouse import additivity as published_additivity

# Same reason as `tests/test_definitions.py`: `just test` runs before
# `dbt deps && dbt parse` in ci.yml, so the manifest is not there yet. CI
# re-runs this file after the parse step — which was untrue from the day this
# line was written until the workflow was corrected to name it, so every count
# cited in the docs went unchecked on every pull request. The claim is a test
# now, in `tests/test_workflows.py`, rather than a comment.
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
#
# **Generated to 99 rather than listed, because the hand-written list stopped at
# "twenty" and that is exactly where a real claim went.** The unit-test total
# moved 29 -> 30 and CLAUDE.md said "twenty-nine unit tests" in two places
# through a whole PR: `\btwenty\s+` cannot match "twenty-nine" (the hyphen is not
# `\s`), and "nine" is below the floor, so the scanner saw nothing and reported
# nothing. A list that has to be extended by hand every time a total crosses a
# decade is a guard with a scheduled expiry; the compound forms are mechanical,
# so generate them.
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
# `of those` / `of the` may sit between the number and the noun — CLAUDE.md and
# DATA_QUALITY.md both write "Eighteen of those tests are dbt unit tests", and a
# strictly adjacent pattern skipped it. Anything longer than that is left out on
# purpose: the further the noun drifts from the number, the more the pattern
# starts matching arithmetic ("367 of the 369 tests" must capture 369, not 367).
_WORD_ALTERNATION = "|".join(sorted(WORDS, key=len, reverse=True))
CLAIM = re.compile(
    rf"\b(\d+|{_WORD_ALTERNATION})\s+(?:of\s+(?:those|the|them|its)\s+)?"
    rf"(?:dbt\s+|data\s+|unit\s+)?tests?\b",
    re.IGNORECASE,
)


def as_int(token: str) -> int:
    return int(token) if token.isdigit() else WORDS[token.lower()]


# Prose that makes these claims. `git ls-files` rather than a glob, so the
# gitignored `docs/sessions/` transcripts (which quote historical counts by
# design) are outside this by construction rather than by an exclude list.
#
# The consequence is worth knowing before you trust a green run on new work: a
# doc that has never been `git add`ed is not tracked, so it is not scanned, and
# every count in it passes by not being looked at. Found by mutation — a stale
# figure planted in a brand-new `docs/` page went uncaught until the file was
# staged. Nothing here can fix that (a glob would drag the transcripts back in);
# stage the file, then trust the run.
SCANNED = (
    "*.md",
    "dbt/models/staging/_unit_tests.yml",
    "dbt/models/marts/_unit_tests.yml",
)


# The additivity figures are also written into two source modules, and both were
# stale. `SCANNED` is markdown plus two ymls because that is where *test* counts
# are written; a docstring is prose too, and the scanner has to be pointed at it.
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

    Not the same as `dbt build --select <model>` reports: that total includes the
    model node itself and any test pulled in by eager indirect selection, which
    is exactly how "22" got written down for a model with 19.
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
)


def owning_model(text: str, pos: int) -> str | None:
    """Which model the prose at `pos` is talking about.

    The nearest *preceding* mention, unbounded, because that is how these
    documents establish context: a heading names the model and everything under
    it is about that model until the next one. A fixed-width window round the
    number does not work — `compliance-models/SKILL.md` writes "This model's 20
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

    Keeping these out of the project-wide set is not fussiness. `attached_to`
    returns 10 for `fct_retail_returns` and 14 for `fct_fx_rates_daily`, and 10
    was the project-wide unit-test total one commit ago — so folding them into
    one set made "10 unit tests" legal anywhere and silently reopened the exact
    staleness this file exists to catch. Scoping them to their own model is what
    lets the set grow without every addition weakening every other check.
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
        # Scanned whole-file, not line by line, because these docs are hard
        # wrapped at ~80 characters and the claims straddle the wraps: the one
        # in `docs/DATA_QUALITY.md` puts "10 unit" at the end of a line and
        # "tests." at the start of the next. A per-line scan is blind to
        # precisely those and passed a mutated unit-test count.
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


# Every integer counting marts. Two things this needs that `CLAIM` does not, both
# found by writing the loose version first and reading what it caught:
#
# * A lookbehind, because these documents quote row counts with thousands
#   separators. `\b(\d+)` matches the "787" inside "808,787" and the "096"
#   inside "4,096" — three false positives, all of them digits mid-number.
# * The head noun, because "mart" is far more often a *modifier* here than a
#   counted thing: "808,787 mart rows" counts rows, "19 mart relations" counts
#   marts. Plural `marts` is unambiguous; singular `mart` only counts when the
#   noun after it says so.
#
# No words-as-numerals form: "seventeen marts" was never written, only "17".
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
    """The same failure as the test counts, one noun over, and it had happened.

    `CLAIM` only reads numbers in front of a test-noun, so "all 17 marts" was
    invisible to it — and stayed written in five places (`README.md`, `CLAUDE.md`
    twice, a skill and a course module) across the commits that added
    `fct_country_weather_year` and versioned `fct_emissions_energy`. By then the
    true figures were 19 nodes over 18 models. Nothing was red: a mart count is
    not a number any build prints, so there was no run that could disagree with
    it.

    No floor on `seen` here, unlike the test-count scan above. Mart counts are
    genuinely rare in this prose — one or two sites, against dozens for tests —
    so a floor would be a number to maintain rather than a guard, and the
    vacuity risk it covers there is covered here by `MART_CLAIM` being three
    words long.
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


# Every integer in front of an additivity label, the release map's shape, and the
# yml entry count. A third noun after tests and marts, and the one that had no
# guard at all: `dim_country` added two `non_additive` labels in the last commit
# of a PR and left the pre-`dim_country` pair standing in four files —
# CLAUDE.md, docs/PRACTICES.md and two source-module docstrings, one of them
# `tests/test_additivity.py`'s own, where `numeric()` fifty lines below already
# returned a different number for the phrase that docstring was describing.
# Written without the stale digits on purpose: this scanner cannot tell a
# quotation from an assertion, and a comment recording an old number would be
# the first thing it failed on.
#
# **The two bases are the reason this needs its own scanner rather than a wider
# `CLAIM`.** The ymls carry 190 literal `additivity:` entries; the manifest
# carries 226 labelled columns, because `fct_emissions_energy_v1` inherits 36
# through `include: all`. Both are true counts of different things, so a set
# holding both would make either legal anywhere and the guard would be worth
# nothing. Each figure is bound to the noun it is written in front of instead.
#
# Deliberately not scanned: a bare "N labels". `docs/PRACTICES.md` writes "34 of
# the 43 labels" about the retail country map, which is a different kind of label
# entirely, so that noun cannot carry a bound.
ADDITIVITY_LABEL = re.compile(
    r"(?<![\d,])(\d+)(?:\s+of\s+(?:the\s+)?(\d+))?"
    # Bounded, and word/space/dash only: it must reach across "of the 226 numeric
    # mart columns are" but never across the `"): "` that separates a digit from
    # a label inside `EXTRA_ADDITIVITY`, or every line of that dict is a claim.
    r"[\w\s—–-]{0,40}?`?"
    r"(semi[-_]additive|non[-_]additive|not[-_]a[-_]measure|additive|EXTRA_ADDITIVITY)\b",
    re.IGNORECASE,
)
# A number in front of the *whole vocabulary* is a total, not a count of the
# first label in the list: `publishing-a-release/SKILL.md` writes "282 published
# columns are labelled `additive` / `semi_additive` / `non_additive` /
# `not_a_measure`". Matched first and its span excluded from the scan above,
# because otherwise every enumeration reads as a claim about `additive` — which
# is how the first run of this guard reported a true sentence as stale.
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

    The count CLAUDE.md quotes when it names the *other* basis, and the one a
    person editing those files would arrive at. Counted as literal keys rather
    than parsed, because "literal entries" is exactly what the prose claims.
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
    """The third noun, and the one whose failure had already shipped.

    A mart count is not a number any build prints, which is what the mart scan
    above exists for. An additivity count is worse: `dbt build` is green whatever
    the prose says, `test_additivity.py` asserts coverage without ever comparing
    itself to the sentence describing it, and the release manifest carries the
    real figure where nobody reads it. Four files agreed on a wrong number
    through a whole review.

    Bound per label rather than against one global set, so "16 are non-additive"
    fails even though 16 is a true count of something.
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
