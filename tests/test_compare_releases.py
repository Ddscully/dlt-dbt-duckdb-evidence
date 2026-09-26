"""Unit tests for the release-to-release volume comparison
(`modern_data_stack.export.compare_manifests`, `publish/compare_releases.py`).

Hand-written manifests in the shape `export()` writes: the rule is the contract,
and every verdict has a case that reaches it and one that just misses it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from modern_data_stack.export import compare_manifests
from publish import compare_releases

WORKFLOW = Path(__file__).resolve().parent.parent / ".github/workflows/release-data.yml"


def manifest(tag: str, **tables: tuple[int, list[int] | None]) -> dict:
    """`manifest("data-x", marts__fct=(100, [2000, 2024]))` — `__` stands for the dot."""
    return {
        "tag": tag,
        "tables": [
            {"table": name.replace("__", "."), "rows": rows, "years": years}
            for name, (rows, years) in tables.items()
        ],
    }


def verdicts(previous: dict, current: dict, max_drop: float = 0.01, exempt=()) -> dict[str, str]:
    return {
        c["table"]: c["verdict"] for c in compare_manifests(previous, current, max_drop, exempt)
    }


def test_a_drop_is_judged_against_the_threshold_from_both_sides():
    """Exactly `max_drop` passes and one row more fails, so `>` against `>=` is pinned."""
    before = manifest("a", marts__at=(100, None), marts__over=(100, None))
    after = manifest("b", marts__at=(99, None), marts__over=(98, None))

    assert verdicts(before, after) == {"marts.at": "ok", "marts.over": "dropped"}


def test_a_table_that_empties_is_dropped_and_one_that_was_empty_is_not():
    before = manifest("a", marts__emptied=(50, None), marts__was_empty=(0, None))
    after = manifest("b", marts__emptied=(0, None), marts__was_empty=(0, None))

    assert verdicts(before, after) == {"marts.emptied": "dropped", "marts.was_empty": "ok"}


def test_growth_is_never_a_loss():
    before = manifest("a", marts__fct=(100, [2000, 2024]))
    after = manifest("b", marts__fct=(10_000, [1990, 2025]))

    assert verdicts(before, after) == {"marts.fct": "ok"}


@pytest.mark.parametrize(
    ("years", "verdict"),
    [
        ([2000, 2023], "narrowed"),  # the latest year went: the failure that matters
        ([2001, 2024], "narrowed"),  # the earliest year went
        (None, "narrowed"),  # the span went, e.g. the year column was renamed
        ([2000, 2024], "ok"),
    ],
)
def test_a_span_that_loses_a_year_at_either_end_is_narrowed(years, verdict):
    """At constant rows, so the row threshold cannot be what fails it."""
    before = manifest("a", marts__fct=(100, [2000, 2024]))
    after = manifest("b", marts__fct=(100, years))

    assert verdicts(before, after) == {"marts.fct": verdict}


def test_a_table_without_a_span_is_never_narrowed():
    before = manifest("a", marts__dim=(100, None))
    after = manifest("b", marts__dim=(100, None))

    assert verdicts(before, after) == {"marts.dim": "ok"}


def test_a_removed_table_is_reported_not_judged(manifests):
    """Retiring a model removes its table on purpose — `fct_emissions_energy_v1` will.

    Asserted on what the command fails on as well as on the label, because
    counting `removed` as a loss keeps every label right.
    """
    before = manifest("a", marts__old=(100, None))
    after = manifest("b", marts__new=(5, None))

    assert verdicts(before, after) == {"marts.new": "added", "marts.old": "removed"}
    text, losses = compare_releases.run(*manifests(before, after))
    assert losses == []
    assert "| `marts.old` | 100 | — | — | — | — | removed |" in text


def test_an_exempt_table_is_never_judged_however_far_it_falls():
    before = manifest("a", analytics__pipeline_tests=(500, None), marts__fct=(100, None))
    after = manifest("b", analytics__pipeline_tests=(1, None), marts__fct=(100, None))

    assert verdicts(before, after, exempt=("analytics.pipeline_",)) == {
        "analytics.pipeline_tests": "exempt",
        "marts.fct": "ok",
    }


def test_the_project_exempts_the_pipeline_tables_and_nothing_else():
    """They count the project's own tests and tables, which shrink when code is deleted."""
    before = manifest(
        "a", analytics__pipeline_tests=(500, None), analytics__co2_intensity=(100, None)
    )
    after = manifest("b", analytics__pipeline_tests=(1, None), analytics__co2_intensity=(1, None))

    assert verdicts(before, after, compare_releases.MAX_DROP, compare_releases.EXEMPT_PREFIXES) == {
        "analytics.co2_intensity": "dropped",
        "analytics.pipeline_tests": "exempt",
    }


@pytest.fixture
def manifests(tmp_path):
    """Writes a previous and a current manifest; returns their paths."""

    def write(previous: dict, current: dict) -> tuple[Path, Path]:
        paths = tmp_path / "previous.json", tmp_path / "current.json"
        for path, content in zip(paths, (previous, current), strict=True):
            path.write_text(json.dumps(content))
        return paths

    return write


def run_main(monkeypatch, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", ["compare_releases", *args])
    try:
        compare_releases.main()
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def test_the_command_fails_on_a_loss_unless_a_person_accepts_it(manifests, monkeypatch, capsys):
    previous, current = manifests(
        manifest("data-2026-09-01", marts__fct=(300, [2000, 2024])),
        manifest("data-2026-10-01", marts__fct=(100, [2000, 2024])),
    )

    assert run_main(monkeypatch, str(previous), "--current", str(current)) == 1
    assert "marts.fct" in capsys.readouterr().err

    assert run_main(monkeypatch, str(previous), "--current", str(current), "--accept-drop") == 0
    assert "accepted a loss in marts.fct" in capsys.readouterr().err


def test_the_command_writes_the_report_to_the_summary(manifests, monkeypatch, tmp_path):
    previous, current = manifests(
        manifest("data-2026-09-01", marts__fct=(100, [2000, 2024])),
        manifest("data-2026-10-01", marts__fct=(101, [2000, 2025])),
    )
    summary = tmp_path / "summary.md"
    summary.write_text("earlier steps' output\n")

    assert (
        run_main(monkeypatch, str(previous), "--current", str(current), "--summary", str(summary))
        == 0
    )
    text = summary.read_text()
    assert text.startswith("earlier steps' output\n"), "the summary is appended to, never replaced"
    assert "against data-2026-09-01" in text
    assert "| `marts.fct` | 100 | 101 | +1.00% | 2000–2024 | 2000–2025 | ok |" in text


def test_an_unchanged_release_says_so_rather_than_printing_an_empty_table(manifests):
    same = manifest(
        "data-2026-09-01", marts__fct=(100, [2000, 2024]), analytics__pipeline_tests=(9, None)
    )
    previous, current = manifests(same, same)

    text, losses = compare_releases.run(previous, current)

    assert losses == []
    assert "No published table changed" in text


def test_the_release_compares_before_it_publishes():
    """A comparison after the upload would report a loss that had already shipped."""
    text = WORKFLOW.read_text()
    package = text.index("- name: Package the artifact")
    compare = text.index("python -m publish.compare_releases")
    publish = text.index("- name: Publish the release")

    assert package < compare < publish
    assert "--accept-drop" in text and "inputs.accept_volume_drop" in text
