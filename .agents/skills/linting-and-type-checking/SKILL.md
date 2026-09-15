---
name: linting-and-type-checking
description: How this repo's SQL linter, Python formatter and type checker behave — sqlfluff through just lint and the dbt templater, ruff through pre-commit only (default rules, combine-as-imports, the pinned markdown scope, --fix deleting noqa-prefixed prose comments), and ty through just typecheck (why not pyright, inline suppressions, why the tree stays at zero). Use when editing .sqlfluff, .pre-commit-config.yaml or ruff/ty settings in pyproject.toml, when a lint, format or ty diagnostic appears, when adding a suppression, or before reformatting files by hand.
---

# Linting, formatting and type checking

SQL and model conventions are `docs/STYLE_GUIDE.md` — naming, grain, import
CTEs, column ordering — with two tables of deliberate departures from dbt Labs'
guides. This skill is how the tools that enforce the formatting half behave, and
where each one is pinned. What pins what across the whole repo is
`dependency-versions`.

## sqlfluff (`just lint`)

The formatting half of the style guide is enforced by `.sqlfluff` through
`just lint`, which is also the pre-commit hook's entry.

- **sqlfluff is pinned exactly, in one place** (`sqlfluff==4.3.0` in
  `pyproject.toml`). Don't restore the upstream `sqlfluff/sqlfluff` hook: it
  installs its own copy, which drifted (3.3.0 there rejected a window-clause
  `order by` the venv's 4.2.2 accepted, so `just lint` passed and the commit
  failed), and it runs from the repo root, where the dbt templater resolves
  `profiles.yml`'s `../data/warehouse.duckdb` one directory too high.
- **Bump the two sqlfluff lines together.** `sqlfluff-templater-dbt` requires
  `sqlfluff==<its own version>`, so a mismatch fails resolution — loudly, for
  once. The `sqlfluff==` line stays because `just lint` calls `sqlfluff`
  directly.
- **CI lints through `just lint`**, so the linted paths are stated once, and
  `.github/actions/setup` installs `just` for every workflow: a `local` hook whose
  entry is a recipe fails with "Executable `just` not found" without it.

## ruff (pre-commit only)

ruff is configured in `pyproject.toml` and run only through pre-commit
(`ruff-check` with `--fix`, then `ruff-format`).

- **No `select`: ruff runs its own defaults**, and the exact `rev` in
  `.pre-commit-config.yaml` is what holds them still (0.9 enabled 59 rules; 0.16
  enables 413). ruff is not in the `dev` group, so pre-commit's copy is the only
  one — the mirror image of sqlfluff. `extend-select` re-adds the 18 rules 0.16
  dropped, so a bump cannot silently stop checking star imports and `== None`.
- **`combine-as-imports = true`**, or ruff splits `from x import a, run as b` and
  shreds `orchestration/assets.py`'s imports: four layers each export a `run()`,
  and it aliases every one.
- **`--fix` deletes a comment that starts `# noqa`, even when it is prose.** Put
  the rule after the explanation (`# TRY004 asks for TypeError, but …`) and keep
  the real directive, with its colon, on the code line.
- **0.16 formats Python blocks inside Markdown, so the hook's scope is pinned
  here.** Upstream's `ruff-format` hook added `markdown` to its file types
  between v0.16.0 and v0.16.7 — a patch bump that would have reformatted the
  deliberately aligned code in `docs/` — so `.pre-commit-config.yaml` sets
  `types_or: [python, pyi, jupyter]` itself. A manual `ruff format .` is not
  scoped, and will rewrite blocks in `docs/` and `README.md`.

## ty (`just typecheck`)

It is **not** in pre-commit or any workflow, which is the whole shape of the
decision.

- **It was chosen over pyright on the install line.** Output was comparable at
  introduction (ty 38 diagnostics in 0.32s, pyright 45 in 4.83s); ty lands in
  `uv.lock` through the `dev` group, while pyright is an unpinned global npm
  binary — the sqlfluff drift again. pyright also could not find `.venv` unaided
  and said nothing, burying real diagnostics under phantom missing imports, which
  is why `[tool.ty.environment].python` is explicit.
- **It is pre-1.0, so `>=` and nothing gates on it.** Diagnostics move between
  patch releases; pin it exactly before putting it in pre-commit or a workflow.
- **Suppressions are inline `# ty: ignore[rule]` beside their reason**, never a
  rules list. There are three: the optional `openpyxl` import in each seed script,
  and `SupportsPipeline.deactivate` in `tests/conftest.py` (declared on
  `Pipeline`, not on the protocol `PipelineContext.pipeline()` returns).
- **The tree is clean, and zero is the point**: a checker that always prints the
  same lines is a checker nobody reads. Getting there showed what noise costs —
  23 of the first 38 diagnostics were `.fetchone()[0]` against aggregates that
  return one row by construction. `modern_data_stack.db` states that invariant
  once (`db.scalar` raises naming the query), and `ingest/http.py`'s
  `get_json_object` narrows `dict | list` once for the sources that only ever
  receive objects.
