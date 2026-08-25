# Contributing

Thanks for looking. This is a demonstration warehouse rather than a product, so
it is worth saying up front what that means for a contribution:

- **Bug reports and corrections are very welcome**, especially a number that is
  wrong, a link that is dead, or a step in the quickstart that does not work on
  your machine. Those are the failures this repo cannot see from the inside.
- **Feature additions are a conversation first.** The scope is deliberately
  fixed — seven public sources, one DuckDB file, no cloud. Open an issue before
  writing code, so neither of us spends an evening on something that does not fit.
- **If you want this shape for your own project**, you do not need to contribute
  at all: [`docs/REUSING_THIS_STACK.md`](../docs/REUSING_THIS_STACK.md) is
  written for exactly that, and covers what carries over and what has to be
  rewritten.

## Getting set up

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # or `brew install uv`
uv tool install rust-just                         # the `just` command runner

just setup                    # uv sync: runtime + dev + orchestration
uv run pre-commit install     # the hooks CI also runs
just run                      # the whole pipeline, ~95s, no credentials
```

`just` has to stay on your `PATH` after setup, not just during it: the sqlfluff
pre-commit hook is a `local` hook whose entry is `just lint`, so a shell without
`just` fails the commit with `Executable 'just' not found`.

## Before you open a PR

```bash
just test           # ~1s, mocked payloads, no network, no warehouse
just test-pipeline  # ~30s, the real modules against checked-in fixtures
just lint           # sqlfluff over dbt/models and dbt/snapshots
```

`just test-pipeline` is what CI runs, so a green run here means a green run
there — it serves every source from `tests/fixtures/ingest/` rather than the
live APIs, which is why a red build means this repo broke and not that a
publisher was down.

`just typecheck` reports ty diagnostics but gates nothing; the tree is currently
clean, so a non-empty report means your change introduced something.

## Things that surprise people

- **`dbt deps` is not optional.** `dbt/dbt_packages/` is gitignored, so a fresh
  clone must install `dbt_utils` before `dbt build`, `dbt parse` or `sqlfluff`
  will work. The `just` recipes handle it; a bare `dbt` command will not.
- **Counts written in prose are tested.** `tests/test_documented_counts.py`
  scans tracked markdown for any number in front of a test-noun and checks it
  against what the dbt manifest actually produces. Adding a single test can turn
  a sentence in three documents red, and that is the point.
- **Every PR is squash-merged**, so one PR becomes one commit on `main`. Group
  work by what makes a single writable summary rather than one PR per branch.
- **Conventions live in [`docs/STYLE_GUIDE.md`](../docs/STYLE_GUIDE.md)** —
  naming, grain, import CTEs, column ordering, and where this project departs
  from dbt Labs' style guide on purpose.
- **Do not commit build output.** `data/`, `dbt/target/`, `dbt/dbt_packages/`
  and `reports/build/` are all gitignored and all regenerable.

## Reporting a wrong number

If a figure on the [dashboard](https://ddscully.github.io/dlt-dbt-duckdb-evidence/)
looks wrong, that is the most useful issue you can file. Please say which page
and which figure, and — if you can — what you expected instead and why. Many of
the numbers are deliberately counter-intuitive (a country's emissions falling on
one basis and rising on another), and the
[Coverage](https://ddscully.github.io/dlt-dbt-duckdb-evidence/coverage) page
exists to explain most of those, but a real error is worth catching.
