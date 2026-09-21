# Decisions

The rest of `docs/` and the skills say how the repo works **now**. This folder
says why it works that way, and what was tried or considered instead, so a
rejected approach stays rejected without the current docs retelling its story.

**Check here before undoing a choice.** If a record covers it, the case for
changing it is a new record that supersedes the old one.

## When a record is worth writing

Write one when a choice was made between alternatives **and the rejected one is
tempting**: someone reading the code would plausibly reach for it. Two things
are not decisions and do not belong here:

- **A fixed defect.** "X was missing until #N" is history git already holds.
  If its failure mode still teaches something, the lesson goes in the doc or
  skill for its area, in the present tense.
- **A general lesson** ("a measurement of one command is not evidence about
  another"). That is a rule, and it stays in `AGENTS.md` or a skill.

The doc the decision affects keeps one present-tense sentence and a link here.

## The format

```
# NNNN. <the decision, as a present-tense sentence>

Status: accepted YYYY-MM-DD (#PR) | superseded by NNNN

## Context        the forces, measured where they were measured
## Decision       what the repo does
## Rejected       each alternative, and why
## Consequences   what it costs, and what would make it worth revisiting
```

- **A record is not edited after it merges**, except its `Status` line. A
  reversal is a new record, and the old one's status becomes `superseded by NNNN`.
- Files are `NNNN-kebab-slug.md`, numbered in the order written, and every one is
  listed below; `tests/test_course.py` fails on one that is not.
- **Old counts go in words the counts guard does not read.** Every tracked
  markdown file is scanned by `tests/test_documented_counts.py`, this folder
  included, so a number in front of "tests" must be today's.

## Index

| # | Decision | Status |
|---|----------|--------|
| [0001](0001-ducklake-over-hive-parquet.md) | The landing zone is a DuckLake catalog, not a hive-partitioned Parquet archive | accepted 2026-08-28 |
| [0002](0002-yearly-sources-as-run-config.md) | WDI and weather take their backfill years as run config, not partitions | accepted 2026-09-17 |
| [0003](0003-coverage-py-over-pytest-cov.md) | Coverage runs as `coverage run -m pytest`, not through pytest-cov | accepted 2026-08-26 |
| [0004](0004-agent-plugins-kept-by-measured-use.md) | A vendor agent plugin stays enabled only while it is measurably used | accepted 2026-09-02 |
| [0005](0005-just-serve-first-container-second.md) | The service is `just serve`; the container stack runs the same recipe | accepted 2026-09-18 |
| [0006](0006-runs-launch-from-the-service-image-id.md) | A run container starts from the service's image ID, not the `mds:local` tag | accepted 2026-09-18 |
| [0007](0007-workflows-run-through-just.md) | The workflows run the pipeline through `just` and one setup action | accepted 2026-09-01 |
| [0008](0008-postgres-majors-are-migrations.md) | Dependabot ignores Postgres major versions, which are migrations | accepted 2026-09-18 |
| [0009](0009-cbam-annex-transcribed-faithfully.md) | The CBAM seeds transcribe the annex faithfully, and the mart handles its defects | accepted 2026-08-09 |
| [0010](0010-cbam-markup-schedule-is-a-seed.md) | The CBAM mark-up schedule is a seed, asserted rather than derived | accepted 2026-08-18 |
| [0011](0011-deploy-is-the-containers-instance.md) | `deploy/` is the container's Dagster instance, and only the container's | accepted 2026-09-20 |
