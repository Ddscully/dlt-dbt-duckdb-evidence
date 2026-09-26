"""Compare this release's row counts and year spans with the previous release's.

Run:  uv run python -m publish.compare_releases <previous manifest.json>
      (after `just export-data`, which writes the current one)

Every gate inside the build reads one build, so a model edit that drops a third
of the countries passes all of them: the smaller table is unique on its grain,
in range and non-null. The previous release's `manifest.json` records every
published table's row count and year span, and `release-data.yml` compares
against it before uploading.

Measured across the five releases from `data-2026-07-30` to `data-2026-09-01`:
no published table ever lost a row or a year at either end, and the largest
change was +0.15%. So one threshold serves every table; they differ only in how
fast they grow, which is not a failure. A deliberate drop is accepted with
`--accept-drop`, which `release-data.yml` takes as a dispatch input.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from modern_data_stack.export import LOSS_VERDICTS, compare_manifests
from publish.export_warehouse import EXPORT_DIR

CURRENT_MANIFEST = Path(EXPORT_DIR) / "manifest.json"

# A fraction of the previous release's rows. Nothing has ever shrunk, so this
# is headroom for an upstream correction, not for normal movement. One country
# is about 0.4% of `marts.fct_emissions_energy`, so losing one shows in the
# report and passes; losing three fails.
MAX_DROP = 0.01

# The pipeline tables count the project's own tests and tables, not data, so
# deleting a model or a test shrinks them on purpose.
EXEMPT_PREFIXES = ("analytics.pipeline_",)


def _years(span: list[int] | None) -> str:
    return f"{span[0]}–{span[1]}" if span else "—"


def _rows(n: int | None) -> str:
    return f"{n:,}" if n is not None else "—"


def report(previous: dict, changes: list[dict]) -> str:
    """A Markdown table of every table whose rows, span or presence changed."""
    moved = [
        c
        for c in changes
        if c["verdict"] not in ("ok", "exempt")
        or c["rows"] != c["previous_rows"]
        or c["years"] != c["previous_years"]
    ]
    heading = f"### Published tables against {previous.get('tag', 'the previous release')}"
    if not moved:
        return f"{heading}\n\nNo published table changed its row count or year span.\n"
    lines = [
        heading,
        "",
        "| Table | Rows before | Rows now | Change | Years before | Years now | Verdict |",
        "|---|--:|--:|--:|---|---|---|",
    ]
    for c in moved:
        before, now = c["previous_rows"], c["rows"]
        change = f"{(now - before) / before:+.2%}" if before and now is not None else "—"
        verdict = f"**{c['verdict']}**" if c["verdict"] in LOSS_VERDICTS else c["verdict"]
        lines.append(
            f"| `{c['table']}` | {_rows(before)} | {_rows(now)} | {change} "
            f"| {_years(c['previous_years'])} | {_years(c['years'])} | {verdict} |"
        )
    return "\n".join(lines) + "\n"


def run(
    previous_path: str | Path, current_path: str | Path = CURRENT_MANIFEST
) -> tuple[str, list[dict]]:
    """The report and the tables that lost data."""
    previous = json.loads(Path(previous_path).read_text())
    current = json.loads(Path(current_path).read_text())
    changes = compare_manifests(previous, current, MAX_DROP, EXEMPT_PREFIXES)
    return report(previous, changes), [c for c in changes if c["verdict"] in LOSS_VERDICTS]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("previous", help="the previous release's manifest.json")
    parser.add_argument("--current", default=CURRENT_MANIFEST, help="this release's manifest.json")
    parser.add_argument(
        "--summary", help="also append the report to this file ($GITHUB_STEP_SUMMARY)"
    )
    parser.add_argument(
        "--accept-drop",
        action="store_true",
        help="report a loss without failing, for a drop a person has checked",
    )
    args = parser.parse_args()

    text, losses = run(args.previous, args.current)
    print(text)
    if args.summary:
        with open(args.summary, "a") as fh:
            fh.write(text)
    if not losses:
        return
    names = ", ".join(c["table"] for c in losses)
    if args.accept_drop:
        print(f"compare-releases: accepted a loss in {names}", file=sys.stderr)
        return
    print(
        f"compare-releases: {names} lost rows or years against the previous release "
        f"(more than {MAX_DROP:.0%} of rows, or a year at either end). If the drop is "
        "deliberate, re-run release-data with accept_volume_drop.",
        file=sys.stderr,
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
