---
name: dependency-versions
description: What pins what in this repo and why — dependabot's five ecosystems, the exact sqlfluff/ruff/setup-uv pins, the three versions nothing watches, requires-python and .python-version, the npm lockfile that cannot be re-resolved from scratch, and the dbt/dagster/Python upper bounds. Use when bumping a dependency or a GitHub action, reviewing a Dependabot PR, editing pyproject.toml, uv.lock, .pre-commit-config.yaml or reports/package.json, or when a resolution is stuck.
---

# Dependency and action versions

Green CI proves nothing about versions — that sentence is the whole section, and
everything below is a way it has already cost this repo something. How the
linter, formatter and type checker those pins serve behave is
`linting-and-type-checking`.


`.github/dependabot.yml` watches five ecosystems — `github-actions` (`/`), `uv`
(`/`), `npm` (`/reports`), `docker-compose` (`/`) and `docker` (`/`) — monthly,
each grouped to a single PR.

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
- **`docker-compose` and `docker` are different ecosystems, and picking the
  wrong one watches nothing.** `docker` matches Dockerfiles, Containerfiles and
  Kubernetes manifests; the compose ecosystem's pattern is
  `(docker-)?compose(-\w+)?(\.[\w-]+)?\.ya?ml` (dependabot-core, checked
  2026-09-17), which the root `compose.yaml` matches. **This repo now needs
  both**, and they do not overlap: compose names the service images, the
  `Dockerfile` the base images, and neither ecosystem can see the other's file.
  `docker-compose` was added 2026-09-17 after a review found `compose.yaml`
  claiming `docker` watched its image tags when nothing did — the comment
  asserted a fact about *another* file, which is the shape no test in this repo
  can see. `docker` followed with the `Dockerfile`, which made the claim true
  for the first time.
  - **Between them they are why the container added no unwatched pin.**
    `docs/RUNNING_AS_A_SERVICE.md` §2 reason 2 predicted an image would add
    versions nothing watches; the answer is these two entries plus
    `tests/test_dagster_instance.py`, which refuses a tag Dependabot could not
    bump (`latest`, a bare name, or a floating `X.Y` where upstream's exact tag
    is `X.Y.Z`). The three that can still only age deliberately are unchanged.
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
- **3.14 is blocked upstream, not by us.** No dependency in `pyproject.toml`
  carries an upper bound of its own; the ceilings are in the packages' metadata.
  dagster-dbt declares `Requires-Python <3.14` (dagster issue #33903), dagster
  declares `Requires-Python <3.15`, and dbt-core ships no 3.14 classifier — dbt
  Labs certifies a Python roughly a year behind. dbt 1.12 sat behind the same
  shape of ceiling, dagster-dbt's `dbt-core<1.12`, until dagster-dbt 0.29.22.
- **`pyarrow` is a runtime dependency and was undeclared until 2026-08-25.**
  DuckDB reaches it for `to_arrow_reader()` (the retail ingest,
  `ingest/sources/retail.py`) *and* for `.pl()` — so a tree without it raises
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
- **There are three dependency groups, and `deploy` is the one no recipe but its
  own installs.** `dev` and `orchestration` are what `just setup` syncs;
  `deploy` (`dagster-postgres`, and with it `psycopg2-binary`) exists only for
  `DAGSTER_HOME=<repo>/deploy`, where Dagster's own storage is Postgres. Keeping
  it out of `orchestration` is what stops a laptop clone installing a database
  driver for storage it does not use — `just deploy-deps` is the opt-in, and it
  names all three groups because `uv sync` computes the whole venv from the ones
  it is given. The mirror of that: against a venv it built, a plain
  `just setup` uninstalls both packages again (measured `--dry-run`,
  2026-09-17), which `docs/RUNNING_AS_A_SERVICE.md` §8 records as a way to break
  a running service.
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
  - **`sqlfluff-templater-dbt` is pinned exactly at 4.3.0 and compiles 1.11 and
    1.12 alike**, but it is the thing to check first on any dbt bump: it is the
    one consumer that cannot move independently, by deliberate design.
- **dbt 1.12 was a no-op as well, measured four ways on 2026-09-15.** It waited
  on dagster-dbt's `dbt-core<1.12` until 0.29.22 (2026-09-11), the release of
  [dagster#34085](https://github.com/dagster-io/dagster/pull/34085), a pure
  bound relaxation. Then:
  - `dbt parse` emits zero deprecation warnings and no hints, and the manifest
    is still v12.
  - `just test-pipeline` and `just materialize` against fixtures both build
    `PASS=561`, as 1.11 did. The summary line gains a `REUSED=` field that
    nothing here parses.
  - **A build on a copy of the real warehouse moved no row count in any
    `history` or `marts` table, and changed no snapshot's `dbt_scd_id`s.** That
    is the check a dbt minor needs and a fixture run cannot make: a changed
    snapshot hash would re-version every row of the tables no rebuild
    reproduces, and a copy of the warehouse is the only safe place to see it.
  - **The deprecation-date promotion still fails parse.** With the date moved
    into the past, `DeprecatedModel` exits 2; with the `flags:` block removed,
    0. 1.12 routes every warning through the `--warn-error` handler, the
    machinery that promotion rides on, and no test covers it.
  - **Except in a checkout that had built on 1.11, which none of those runs
    could see.** 1.12 writes a snapshot's run SQL under a directory named after
    its file, `run/<project>/snapshots/<file>.sql/<file>.sql`, where 1.11 wrote
    the `.sql` file itself. So a `dbt/target/` last written by 1.11 fails both
    snapshots with `[Errno 20] Not a directory` and skips everything downstream
    of them. It surfaced 2026-09-17, two days after the upgrade merged, when a
    build first wrote into such a directory. CI, `just test-pipeline` and the
    course sandbox each build into a directory 1.12 created, so none of them
    can. Reproduced against a copy of the stale `run/`: `dbt snapshot` gave
    `ERROR=2`, and `PASS=2` once `run/` was deleted. The fix is deleting
    `dbt/target/run`, or `just clean`, which takes all of `dbt/target`. **A bump
    measured only in fresh directories has not been measured in the ones
    contributors already have.**
- **A re-lock across an exact pin is a silent no-op unless the pinned package is
  named.** dagster-dbt, dagster-dlt, dagster-webserver and dagster-graphql pin
  `dagster==` exactly, so from 1.11's lock `uv lock --upgrade-package dbt-core`
  and `uv lock --upgrade-package dagster-dbt` each print `Resolved 154
  packages`, exit 0 and change nothing: the locked dagster holds the locked
  dagster-dbt, which holds dbt. Naming `dagster` as well frees the family —
  `uv lock --upgrade-package dagster-dbt --upgrade-package dagster
  --upgrade-package dbt-core` moved all seven dagster packages and dbt with them.
  **Read the `Updated` lines, never
  the exit code.**
- **dbt-core 1.12's "parser" is dbt v2, 174 MB of it, and a default run never
  calls it.** `dbt-core-experimental-parser` is an unconditional dependency whose
  one executable in `.venv/bin/` answers `--version` with `dbt-oss 2.0.1`, the
  v2 open-source engine. The locked sdist is 4.8 KB: its build step downloads
  the wheel from the same GitHub release as `dbt-core` 2.0.0rc4 and checks it
  against an embedded sha256, so a `uv sync` reaches github.com as well as PyPI.
  With the binary hidden, a default `dbt parse` exits 0, and `--use-v2-parser`
  fails with `Fusion parser command not found`. With `metricflow` (3.7 MB) and
  `rapidfuzz` (12 MB) replacing `dbt-semantic-interfaces`, 1.12 costs about
  190 MB of venv.
  - **Its one use here is measuring the distance to v2**, and on 2026-09-15
    `dbt parse --use-v2-parser` failed on 224 locations with a single code,
    `UnusedConfigKey (dbt1060)`: 219 `meta:` blocks, plus source `freshness:`
    and `loaded_at_field:`, that v2 wants under `config:`. 1.12's own parse warns
    about none of them, so a clean v1 parse is not v2 readiness. The `meta`
    blocks are the additivity and PII labels that Python reads out of the
    manifest, so the move is more than a yml rewrite.
  - **Moving to v2 drops the package, not the weight.** `dbt-core` 2.0.0rc4
    installs as 200 MB in four packages and reports the same `dbt-oss 2.0.1`;
    what goes is the Python stack around it. The rest of the move is unmeasured:
    DuckDB is a beta, CLI-only adapter in v2, `sqlfluff-templater-dbt` cannot
    template it (`dbt lint` ships only in the proprietary `dbt` distribution),
    and dagster-dbt already reads v2's event stream (`DbtFusionCliEventMessage`).
  - **Leaving it out would take an override, and overrides stay refused.**
    `[tool.uv] override-dependencies` could also have forced 1.12 before the cap
    lifted. Either would be a **fourth entry in the three-versions table**: an
    override is as invisible to `lockfile-only` as a `==`, so nothing would
    report when it turned from a workaround into the thing holding dbt back.
  - **A semantic layer can now be written against 1.12's spec**, which reworks
    the Semantic Layer YAML and adds `osi_document.json`. Authoring one against
    1.11's would have meant migrating it almost at once.
- **dbt 1.12 reads a `.env` from its working directory, which here is `dbt/`,
  so every recipe that writes refuses while one exists.** Measured against a
  copy: a `.env` there naming `WAREHOUSE_PATH` sent `dbt debug` to that file —
  and DuckDB created it — while the shell left the variable unset, as most
  recipes do; a shell value still wins. `just where` reads only the shell, so it
  would have named the default file while dbt wrote to another. The repo-root
  `.env` is the place for such values: `dotenv-load` exports it to every recipe,
  and `where` prints it.
  - **The refusal is the private recipe `_no-dbt-dotenv`**, which `where`
    depends on. `test-pipeline` and the course recipes export their own
    `WAREHOUSE_PATH` and skip `where`, so each depends on it directly — the one
    place a writing recipe repeats it by hand, and
    `tests/test_workflows.py` fails a writing recipe that reaches neither.
  - **Recipes that run dbt without writing are not covered**: `dbt-unit-test`,
    `dbt-freshness`, `dbt-docs` and `lint` depend only on `dbt-deps`, so a
    `.env` in `dbt/` still points them at another file, silently.
- **"Lightweight" is a measured claim, and the dev tooling was most of the
  weight.** Removing harlequin and marimo on 2026-08-25 took the tree from 198
  packages to 153 and the venv from 1.1 GB to 736 MB — a third of it — for two
  tools that duplicated capability the stack already had: the DuckDB CLI
  replaces harlequin (`just sql`, read-only by default), and marimo cost 122 MB
  plus jedi/loro/pyzmq to render one `select *`. What was left *was* the stack
  — polars 206 MB, pyarrow 137 MB, duckdb 58 MB, dagster 62 MB — until dbt
  1.12's parser binary (above).
  - **The venv is not where this repo's disk goes**, which is worth knowing
    before optimising it again. `reports/node_modules` alone is 931 MB and the
    regenerable build output under `data/`, `dbt/target` and `reports/` is
    ~1.2 GB more. `just clean` reclaims those; `just clean deep` also drops
    `node_modules`.
  - **`data/warehouse.duckdb` is the one target `just clean` must not treat as
    derived.** It holds the `history` schema — see *Snapshot history* in
    `AGENTS.md` — which no
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
