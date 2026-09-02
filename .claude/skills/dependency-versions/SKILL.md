---
name: dependency-versions
description: What pins what in this repo and why — dependabot's three ecosystems, the exact sqlfluff/ruff/setup-uv pins, the three versions nothing watches, requires-python and .python-version, the npm lockfile that cannot be re-resolved from scratch, and the dbt/dagster/Python upper bounds. Use when bumping a dependency or a GitHub action, reviewing a Dependabot PR, editing pyproject.toml, uv.lock, .pre-commit-config.yaml or reports/package.json, or when a resolution is stuck.
---

# Dependency and action versions

Green CI proves nothing about versions — that sentence is the whole section, and
everything below is a way it has already cost this repo something. The linter
policy those pins serve is in `CLAUDE.md` under *Style guide*.


`.github/dependabot.yml` watches three ecosystems — `github-actions` (`/`), `uv`
(`/`) and `npm` (`/reports`) — monthly, each grouped to a single PR.

- **It exists because green CI proves nothing about versions.** Every action sat
  on a Node 20 major for months while `ci.yml` passed, until the runners started
  warning that they were forcing those actions onto Node 24. Nothing in the repo
  could have said so.
- **The `uv` entry is `versioning-strategy: lockfile-only`, and must stay that
  way.** The bounds in `pyproject.toml` are minimum-supported versions, not pins
  (`duckdb>=1.1`, `polars>=1.17` sit far below what resolves). Dependabot's
  default for uv raises the lower bound instead, and its first run did exactly
  that — `dlt[duckdb]>=1.5` to `>=1.29.1`, dropping two dozen releases for no
  change to what installs, since `uv.lock` already resolved there.
- **`npm` is deliberately left on the default.** Those are `^` ranges, so the
  major *is* the pin and an Evidence 40 → 41 bump is a real upgrade worth seeing
  as a PR. `lockfile-only` there would quietly cap the site at 40.x forever.
- **`reports/package.json` cannot be resolved from scratch, and the committed
  lockfile is what hides it.** A fresh `npm install` against the *unmodified*
  file fails with `ERESOLVE` on a `typescript` peer conflict between
  `svelte-preprocess` and `svelte2tsx` — on npm 9 **and** npm 11, so it is not
  an old-npm artifact. `npm ci` works only because it never re-resolves, which
  is why `publish/build_report.py` prefers it on a cold checkout and why nothing
  has ever noticed. Editing that file therefore needs `npm install --force`.
  - **`--legacy-peer-deps` is the trap, because it fails later and elsewhere.**
    It resolves, installs, and prints nothing alarming — then `evidence build`
    dies with `Could not resolve peer dependency "@sveltejs/vite-plugin-svelte"`,
    because the flag skips peer installation. `--force` keeps the peers and
    tolerates the conflict, which is the one that produces a tree that builds.
  - **Reconcile the existing lock; never delete it.** `npm install --force` with
    the committed `package-lock.json` in place gave a purely subtractive diff —
    460 packages removed, **0 added, 0 version-changed**, and ten metadata-only
    edits (the root dependency list, plus nine packages gaining `"peer": true`
    now that they are reachable only as peers). Deleting the lock and
    re-resolving also works and costs 189 MB, but re-picks every transitive
    version in the tree, which is an unreviewable diff for a size change.
    **Check the parsed structure, not git's line count**: the textual diff reads
    1,950 insertions against 7,903 deletions, which looks like a rewrite and is
    not one — comparing the `packages` objects is what shows nothing moved.
- **The site shipped 14 source connectors and used one.** `package.json` was the
  stock Evidence template — bigquery, databricks, mssql, mysql, postgres,
  snowflake, sqlite, trino, motherduck, csv and source-javascript, for a project
  whose `evidence.config.yaml` registers `@evidence-dev/duckdb` and nothing else,
  so the other eleven were never even loaded. The connector packages are ~1 MB
  each; the cost is what they drag behind them — `mssql → tedious →
  @azure/identity` is 72 MB and `snowflake → snowflake-sdk → @aws-sdk/client-s3`
  another 24 MB with `@smithy`. Trimmed 2026-08-25: **931 MB → 694 MB**.
  - Three of the four `overrides` (`sqlite3`, `jsonwebtoken`, `axios`) were
    security pins on connector transitives and had nothing left to override.
    Only `trim` still resolves to a package in the tree.
  - **Evidence itself was already current** and is easy to mistake for the
    problem: 40.1.8 *is* `latest`, with `core-components` 5.4.2 and `duckdb`
    2.0.1 likewise. There is no 41. The weight was never the framework.
- **Dependabot scans the moment the config lands**, not on the next scheduled
  date — expect PRs immediately after touching that file.
- **A yanked release stays locked until something re-resolves.** `uv.lock` held
  `polars==1.43.1` after upstream yanked both it and 1.43.0; nothing said so
  until an unrelated `uv lock` printed the warning in passing. Installs of a
  yanked version keep working — that is the point of a lockfile — so the monthly
  Dependabot PR is the only thing that would have cleared it, up to a month
  later. `uv lock --upgrade-package <name>` is the targeted fix and leaves the
  other 196 packages alone. Worth reading the warnings on any re-lock, since
  that is the one moment they appear.
- **`astral-sh/setup-uv` is pinned to an exact patch, not a major.** It stopped
  publishing moving major/minor tags at v8 as a supply-chain measure, so `@v9`
  does not resolve at all. It is used in exactly one place —
  `.github/actions/setup/action.yml`, the composite action all four workflows
  call — and the comment saying so lives beside it, because the obvious tidy-up
  is to "simplify" it back to a major. It was in four workflows until
  2026-09-01, which is four places for a Dependabot bump to disagree with
  itself.
- **`pages.yml` is the only workflow that needs Node** (24; the Evidence build).
  The other three run on a bare uv checkout — see the Orchestration section for
  why the site is excluded from `full_refresh`.
- **`pages.yml` triggers on a path *allowlist*, and `paths-ignore` would have
  been wrong.** It is a full ingest → dbt → Polars → lake → Evidence run against
  the **live** public APIs, and it fired on every push to `main` until
  2026-08-26: 23 of the 94 commits on `main` have been documentation, skills or
  testing, and each one rebuilt and redeployed an identical site for the same
  Actions minutes and the same API load as a data change. The obvious
  `paths-ignore: ['**.md']` is the trap — `reports/pages/` is ten markdown files
  and they *are* the dashboard, so a blanket markdown ignore would stop
  republishing the site exactly when a page changed. An allowlist gets the two
  mistakes the right way round: a build input left out of it makes the site
  stale, which is visible and which the weekly cron caps at seven days, where a
  doc directory left out of an ignore list is invisible and permanent.
  `.dagster/dagster.yaml` is on the list because `DAGSTER_HOME` is the
  checked-out `.dagster/`, so that file configures the live run rather than a
  runner default. `tests/test_workflows.py` holds the list to the tree.
- **Python is 3.13, set in one place: `.python-version`.** No workflow passes a
  `python-version` to `setup-uv`, so that file is what CI, the release job and a
  contributor's venv all read. Nothing watches it — Dependabot covers
  `github-actions`, `uv` and `npm`, none of which see it, so the interpreter is
  one of the three versions here that can only age deliberately; the other two
  are three bullets down. It sat on 3.12 from the initial commit to 2026-08-10
  for no reason anyone recorded.
- **`requires-python` tracks it (`>=3.13`), and here that bound is *not* a
  minimum-supported floor** — the opposite of the dependency bounds above, so
  the exception is worth knowing. This is an application, not a library: it
  ships a committed `uv.lock` and a `.python-version`, and nothing installs it
  as a dependency. A floor below what CI builds is a promise no job tests. It
  was briefly `>=3.12` against a 3.13 `.python-version` and that is the state to
  avoid — CI reads `.python-version`, so 3.12 was claimed and never exercised.
- **`requires-python` is what `uv.lock` resolves against, so it is not
  cosmetic.** Raising it to `>=3.13` dropped a package (`win-precise-time`) and
  ~400 lines of `python_full_version < '3.13'` marker branches. Lowering it
  again is a re-lock, not an edit.
- **Three versions here can only age deliberately, and the other two are the CI
  linters.** The `.python-version` bullet above used to claim a set of one. The mechanism is
  different in each case, which is why none of them ever shows up as a
  Dependabot PR that failed to arrive:

  | Version | Why nothing watches it |
  |---|---|
  | `.python-version` | no ecosystem covers the file at all |
  | `sqlfluff` + `sqlfluff-templater-dbt` | `versioning-strategy: lockfile-only` edits `uv.lock` and never relaxes a `==` in `pyproject.toml`, so exactly one resolution stays valid forever |
  | `ruff` | pre-commit is not a watched ecosystem — `grep -c pre-commit .github/dependabot.yml` is 0, and `pre-commit autoupdate` is its only mover |

  That both CI linters land in this category is a coherent policy — a gate
  should move when a person decides, which is the whole argument for pinning
  them. It is also the *cost* of pinning, and the inverse of the sentence this
  section opens with: green CI proves nothing about versions, and an exact pin
  then makes the package invisible to the one mechanism that would have said it
  moved. Somebody has to remember instead. This bullet is that somebody.
- **Ruff's target version is inferred from `requires-python`**, with no
  `target-version` in `[tool.ruff]` — so that one line also decides which
  rewrites the linter will make. It reports `3.13` now; it reported `3.12`
  before, which is why nothing 3.13-only could have been written in the gap
  even by accident.
- **3.14 is blocked on dbt, not on us.** `uv lock --python 3.14` resolves, but
  that only proves the solver is happy — dbt-core ships no 3.14 classifier,
  and dbt Labs certifies a Python roughly a year behind. `dagster<3.15` is the
  only hard upper bound in the tree.
- **`pyarrow` is a runtime dependency and was undeclared until 2026-08-25.**
  DuckDB reaches it for `to_arrow_reader()` (the retail ingest,
  `ingest/pipeline.py`) *and* for `.pl()` — so a tree without it raises
  `ModuleNotFoundError: pyarrow` from the ingest and from **both** Polars
  transforms. `.pl()` is the surprising half: polars itself doesn't need
  pyarrow, DuckDB's bridge to it does.
  - **It was invisible because uv installs the `dev` group by default.**
    `[tool.uv] default-groups` is commented out, so uv's own default (`dev`)
    applies and every `uv sync` in the justfile and all four workflows pulled
    harlequin — whose `textual-fastdatatable` dragged pyarrow in. The declared
    runtime set was incomplete for as long as it was undeclared and nothing
    could say so. `uv sync --no-default-groups` is what reproduces it.
  - The lesson generalises past this package: a dependency that arrives as some
    dev tool's grand-transitive is indistinguishable from a declared one until
    the dev tool leaves.
- **Dropping harlequin is what unblocked dbt 1.11, and the mechanism is an exact
  pin.** harlequin pins `click==8.1.8`; dbt-core 1.11 requires `click>=8.3.0`,
  so the SQL IDE in the dev group was holding the transformation engine a minor
  version back. Bundled with harlequin still in, reaching dbt 1.11 also forced
  `textual` across two majors; with it gone, textual leaves the tree entirely.
  Read a stuck resolution as "who pins this", not "the solver is being careful"
  — `uv tree --invert --package <name>` names the culprit, and an exact `==` in
  a *transitive* is the shape to look for.
- **dbt 1.11 was a no-op for this project, and the parse log is the evidence.**
  `dbt parse` emits zero deprecation warnings on it. That is the
  `data_tests:`/`arguments:` discipline paying out: 1.11 defaults
  `require_generic_test_arguments_property` to True, which is the spelling this
  project already used. Its other deprecations — `--models`/`-m`, source
  `overrides:`, `{{ modules.itertools }}` — appear nowhere here, and its new
  jsonschema warnings are gated to Snowflake/Databricks/BigQuery/Redshift, so
  DuckDB never sees them.
  - **The manifest stays schema v12**, so `dagster-dbt`, `transform/pipeline_status.py`
    and `tests/test_documented_counts.py` all read it unchanged. Worth checking
    rather than assuming on any dbt minor: three consumers here parse it.
  - **1.12 is blocked on dagster-dbt, not on us** — it requires `dbt-core<1.12`,
    and the newest release (0.29.19) still does. Same shape as the 3.14 bullet
    above, one layer over. Everything else here is already ready for it:
    resolved without dagster-dbt, `dbt-core 1.12.3`, `dbt-duckdb 1.11.0` and
    `sqlfluff-templater-dbt 4.3.0` land together cleanly, so the day the cap
    lifts this is a re-lock and nothing else.
    - **Track [dagster#34085](https://github.com/dagster-io/dagster/pull/34085)**
      ("Allow dbt-core 1.12", open since 2026-08-06; issue
      [#34014](https://github.com/dagster-io/dagster/issues/34014)). It is a
      *pure bound relaxation* — `<1.12` to `<1.13`, no code changes — and its
      author verified the thing the bullet above says to check: **the manifest
      stays schema v12**, all 23 `dbt.*`/`dbt_common.*` import sites still
      resolve, and dagster-dbt's own CI reports identical pass/fail on 1.11.12
      and 1.12.0. A maintainer has acknowledged it with no timeline.
    - **So the cap is known-conservative, and forcing it is still refused.**
      `[tool.uv] override-dependencies` would install 1.12 today and upstream's
      test matrix is the evidence it works. It buys nothing — nothing 1.12
      removes (`dbt login`, the bundled dbt-state plugin, `--manage-state`) is
      used here — and it costs a **fourth entry in the three-versions table**:
      an override is exactly as invisible to `lockfile-only` as a `==`, so when
      #34085 merges nothing would report that the override had turned from a
      workaround into the thing holding dbt back.
    - **What would change that is adding a semantic layer.** 1.12 reworks the
      Semantic Layer YAML spec and adds `osi_document.json`; this project has
      no semantic model or metric yet, so authoring one against 1.11's spec
      would mean migrating it almost immediately. That is the one piece of work
      whose value here is worth reopening the override question for.
  - **`sqlfluff-templater-dbt` is pinned exactly at 4.3.0 and compiled 1.11
    fine**, but it is the thing to check first on any dbt bump: it is the one
    consumer that cannot move independently, by deliberate design. 4.3.0
    resolves against dbt-core 1.12 when nothing holds it back, so the cap that
    keeps this tree on 1.11 is `dagster-dbt`'s, not the templater's.
- **"Lightweight" is a measured claim, and the dev tooling was most of the
  weight.** Removing harlequin and marimo on 2026-08-25 took the tree from 198
  packages to 153 and the venv from 1.1 GB to 736 MB — a third of it — for two
  tools that duplicated capability the stack already had: the DuckDB CLI
  replaces harlequin (`just sql`, read-only by default), and marimo cost 122 MB
  plus jedi/loro/pyzmq to render one `select *`. What is left *is* the stack:
  polars 206 MB, pyarrow 137 MB, duckdb 58 MB, dagster 62 MB.
  - **The venv is not where this repo's disk goes**, which is worth knowing
    before optimising it again. `reports/node_modules` alone is 931 MB and the
    regenerable build output under `data/`, `dbt/target` and `reports/` is
    ~1.2 GB more. `just clean` reclaims those; `just clean deep` also drops
    `node_modules`.
  - **`data/warehouse.duckdb` is the one target `just clean` must not treat as
    derived.** It holds the `history` schema — see *Snapshot history* in
    `CLAUDE.md` — which no
    rebuild reproduces. `just clean warehouse` mirrors
    `publish/restore_history.py`: it gates on *whether there is history to
    lose*, not on how alarming the file looks, so an empty `history` schema
    goes without ceremony and a populated one needs `--force`.
  - **A refusal has to be a no-op, and this one was not.** The gate started out
    after the safe-tier deletion, so a refused `just clean warehouse` still took
    1,028 MB with it on the way to saying no. The check now runs before anything
    is removed. Worth generalising: a guard placed after the cheap work is a
    guard on only half the command.
  - **`[ "$held" -gt 0 ]` on an empty `$held` fails *open*, which is the
    dangerous direction.** It is an error, but inside an `if` an error reads as
    false and `set -e` does not fire — so a warehouse whose history could not be
    counted (locked by a Dagster run, file corrupt) would have fallen through to
    the delete. It fails closed now, and `--force` deliberately does not
    override that case: the check cannot tell a corrupt file from a locked one,
    and deleting the locked one is the worse mistake.
