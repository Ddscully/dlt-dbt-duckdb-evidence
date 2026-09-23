---
name: repo-guards
description: The hand-maintained lists in this repo and the tests that hold them to the tree — SOURCE_TABLES, RAW_DESCRIPTIONS, WB_WDI_INDICATORS, ATTRIBUTION, pages.yml's path allowlist, the eight asset-check bodies, counts cited in prose — plus the offline fixture dispatch table behind INGEST_FIXTURES. Use when adding or editing one of those lists, writing a guard for a duplication, re-recording fixtures, or when a citation, count, workflow-path or fixture test fails.
---

# Hand-maintained lists, their guards, and the fixtures (`tests/`)

Every list in this repo that restates something another file already knows is a
place two files can disagree in silence. None of these failures is loud: an
unlisted source yields no row and the page under-reports while looking complete,
an unlisted resource materialises with no description, a stale count reads as
authoritative. Each one below is now asserted against the authority it copies.

The two-tier test split and `nightly.yml` stay in `AGENTS.md` under *Testing*,
and coverage is `tests/README.md`. The traps in the suite itself are the last
section here. The mutation method these guards were written with is in the
`unit-testing-dbt-models` skill.

## The lists and what holds them

- **The additivity labels are a judgement per column, and the guard is what
  stops the judgement going missing.** Every numeric mart column carries
  `meta: {additivity: …}`; `tests/test_additivity.py` asserts four properties and
  each was mutation-proven when it was written — drop a label, use a fifth
  value, call `co2_per_capita` additive, delete a `semi_additive` description,
  label a varchar, and a different one of the five fails each time. The one
  worth understanding is **`test_a_ratio_shaped_name_is_never_summable`**: the
  other assertions can only see a label that is *absent*, and a wrong label is
  as present as a right one. A name carrying `_pct`, `_per_`, `share`, `rate`,
  `intensity`, `median_`, `avg_` or `price` is a ratio by construction, so
  declaring one summable is a contradiction the tree can check — and it holds
  today with no exceptions, which is what makes it an assertion rather than a
  paragraph. It is one-directional on purpose: plenty of non-additive columns
  are not ratio-named (`temp_mean_c`, `longest_gap_days`, `n_customers`), and
  requiring the converse would claim the pattern is a complete theory of
  measures.

- **Two lists re-enumerated the seven dlt resources with no covering test, and
  both fail green.** `SOURCE_TABLES` (`transform/pipeline_status.py`) is
  iterated by `observability.build_sources`, so an unlisted source yields no
  row and the pipeline page under-reports while looking complete — the exact
  symptom `adding-a-data-source` records from a *different* cause (the Arrow path's
  missing `_dlt_load_id`, "six sources for seven"). `RAW_DESCRIPTIONS`
  (`orchestration/assets.py`) is read with `.get(name)`, so an unlisted
  resource materialises with no description at all. Both are now held to
  `public_indicators()`, the same authority `load_groups`,
  `YEAR_RANGE_RESOURCES` and `PARTITIONED_RESOURCES` use.
  - **`SOURCE_TABLES` is asserted, not derived, and the layering is the
    reason.** Deriving deletes the list but makes `transform/` import from
    `ingest/`, which no transform module does — and `pipeline_status` is the one
    module that must run *after* dbt rather than beside ingestion. Coupling it
    to the ingest layer at runtime to avoid restating seven strings is the worse
    trade, and asserting is what every other list here already does.
  - **Its guard cannot live in `tests/test_pipeline_status.py`**, which is the
    obvious home: that module has an **autouse** fixture replacing
    `SOURCE_TABLES` with a one-name stub, so a guard written there would assert
    against the stub and be green forever. It sits in `tests/test_ingest.py`
    beside the identical `load_groups` assertion, which is where the authority
    already is and where no patch reaches it.
  - **`RAW_DESCRIPTIONS` can only be asserted** — prose is not derivable — and
    its guard is in `tests/test_definitions.py`, which already carries the
    manifest skipif and is re-run by CI after `dbt parse`. Putting it in
    `test_ingest.py` would drag a dagster import and a skip into the one module
    that runs clean in a fresh clone.
  - **A rename fires the *missing* assertion, never the *stale* one, so the
    orphan branch needs its own mutation.** Renaming a key produces both a gap
    and an orphan and the first assert wins, which leaves the second measuring
    nothing; adding a key without removing one is what isolates it. Same lesson
    as the retired `lake_matches_warehouse`' two drift cases and the FX
    partitioning fixture: a mutation that moves both ends at once cannot tell
    you which end is guarded. A key check alone also passes a whitespace-only description,
    which renders as the same blank a missing key does, so the value floor is a
    `.strip()` rather than truthiness.
- **Two more restatements were found and closed the same way, and choosing
  *them* was a scoping decision rather than a survey result.** The rule now is
  that a candidate must be a **live source**, a **published artifact** or a
  **hand-maintained duplication** — a unit test over the retail archive guards
  code nobody is editing against data that closed in December 2011. These two
  clear it; more retail tests did not.
  - **`WB_WDI_INDICATORS` against `stg_wdi.sql`.** The dict maps eleven
    indicator codes to column names and the model says it again as
    `max(case when indicator = 'X' then value end) as Y`. Written twice,
    nothing tying them, while *Adding a WDI indicator* in `adding-a-data-source`
    invites the edit.
    **The dangerous drift is the one where both sides agree on the keys**: swap
    `NY.GDP.MKTP.CD` for `.KD` and current-dollar GDP lands in
    `gdp_constant_usd`, which every intensity figure divides by. every data
    test on `stg_wdi` is an `accepted_range`, and both series
    are non-negative USD, so nothing goes red — and per the GDP bullets in
    `AGENTS.md` that substitution flips the decarbonisation *sign* for 30 countries. A guard
    written as `set(a) == set(b)`, the obvious form, passes it.
  - **It parses the SQL, not the manifest**, which is why it has no skipif.
    `just test` runs *before* `dbt deps && dbt parse` in `ci.yml`, so a
    manifest-based guard would skip itself in a fresh clone — the one place it
    is most needed. `tests/test_documented_counts.py` pays that cost; this
    doesn't have to.
  - **`ATTRIBUTION` against `ALL_URLS` and the README.** Sources tie to
    `ALL_URLS` because that is already the authority for what the pipeline
    fetches and already carries its own guards. **A fetch host is not a
    publisher and neither string is derivable from the other** — OWID is
    fetched from `raw.githubusercontent.com` and credited at `github.com/owid`,
    the World Bank from `api.worldbank.org` and credited at
    `data.worldbank.org`, the euro rates via `api.frankfurter.dev`, which is
    credited *beside* the ECB rather than instead of it. So
    `PUBLISHER_FOR_FETCH_HOST` states the mapping and is itself held to
    `ALL_URLS` both ways. The CBAM seeds are the one credited row it cannot
    reach: transcribed from a regulation rather than fetched, so no URL
    represents them.
  - **Licences match on the markdown label *or* the URL, and that is
    load-bearing rather than defensive.** `ATTRIBUTION` links
    `[CC BY 4.0](…)` and the README writes the bare words with no link, so a
    URL-only comparison fails today on a difference that is entirely
    legitimate. Taking the label out of the document also avoids a vocabulary
    of known licences, which would go stale the first time a source arrived
    under one nobody had listed.
  - **Both weaknesses in the first draft were found by mutation, not review,
    and both had already gone green.** Substring-searching the whole document
    for a publisher passes on a *licence* URL that contains it —
    `ec.europa.eu/eurostat` sits inside the Eurostat copyright-notice link, so
    deleting Eurostat as a source was invisible. And `rows[1:]` skips the table
    header by position, so deleting the header line silently drops the first
    *source* instead — the row nothing else would mention. Generalising: a
    substring check over a document full of links is nearly always too weak,
    because URLs contain the names being searched for; and positional parsing
    of hand-written markdown fails by quietly checking one row fewer rather
    than by erroring. Matching the header by content and asserting it fixed the
    second and caught a Publisher/Licence column swap for free.

- **`pages.yml`'s path allowlist is a hand-maintained list like any other, and
  `tests/test_workflows.py` holds it to the tree.** Every tracked file must be
  claimed by exactly one of the workflow's `paths:` or the test's
  `NOT_A_SITE_INPUT`; a path claimed by neither is a red test asking someone to
  decide whether the published site is built from it. **It found six files on
  its first run** — the community-health templates (`CONTRIBUTING.md`,
  `CODE_OF_CONDUCT.md`, the issue forms) that landed in `.github/` one commit
  earlier. Three details are load-bearing:
  - **Overlap is forbidden, not merely redundant.** A deny pattern that also
    covered an allowed path would keep the file green after the allow entry was
    deleted, which is precisely the drift the guard exists for.
  - **The stale direction needs its own assertion**, because a rename fires the
    *unclassified* one and stops there — the same finding as the
    `RAW_DESCRIPTIONS` guard, and the same fix.
  - **The `paths:` block is scanned, not parsed with PyYAML**, which is not a
    declared dependency here (it arrives as dbt's transitive, and this repo has
    already been bitten by exactly that shape — see the `pyarrow` bullet). A
    scan that stops matching passes by not looking, so the vacuity guard is a
    real test and the scan returns empty rather than raising, to make its
    message the one a reader sees.
- **The eight `@dg.asset_check` bodies are unit tested in
  `tests/test_asset_checks.py`.**
  `tests/test_definitions.py` proved each check was *registered* — that it would
  run at all. Nothing proved any of them would *notice*: the bodies were only
  ever executed by a full materialize, so `just test` could not tell a working
  check from one whose logic had inverted, and the answer arrived minutes later
  in `just test-pipeline` instead of in the `just test` loop.
  `AssetChecksDefinition` is callable and none of the eight take a `context`, so
  no execution harness is needed — point the module's `DUCKDB_PATH` (or
  `LAKEHOUSE_DIR`, or `page_routes`) at a throwaway, call the check, read the
  `AssetCheckResult`. **Anything under `tests/` importing the orchestration layer
  needs the dlt teardown fixture**, and `test_asset_checks.py` shipped an
  identical copy of it alongside `test_definitions.py`'s rather than sharing
  one — a code review caught the duplication and it now lives once, in
  `tests/conftest.py`.
  - **Ten mutations, nine caught, and the one survivor is the finding** — it is
    dead code, and provably so. Both of the first round's survivors were worth
    chasing rather than papering over: one was a redundant clause, the other a
    fixture that could not isolate what it claimed to.
  - **`co2_intensity_rank_is_dense` states a condition that cannot be reached.**
    Deleting `min(co2_intensity_rank) <> 1` changes no outcome: if
    `max == count(distinct) == k`, the k distinct values are positive integers
    all <= k, so they are exactly {1..k} and the minimum is necessarily 1.
    Brute-forced over every multiset drawn from 1..8 up to length 6 — nothing
    reaches the term. It is the fourth unreachable branch recorded in this file,
    after `dim_date`'s eleven unbuilt fiscal policies, the retail
    `<> 'adjustment'` clause and `period_is_complete`'s boundary, and the only
    one that is *provably* unreachable rather than merely unreached by the data.
    The clause stays, because it states the intent; the docstring next to the
    case says so, to save the next reader an afternoon on a fixture that cannot
    exist.
  - **`weather_revisions_are_derivable` guards a wrong *number*, not an
    exception, and that is what makes its fixtures unusual.** The check exists
    because dlt rewrites `_dlt_id`/`_dlt_load_id` on every merged row, so a
    revision log built without projecting those away returns the whole table —
    a plausible count, in the right shape, that reads as a catastrophic upstream
    restatement. Nothing raises. So the zero is asserted as hard as the one:
    `test_an_identical_reload_yields_no_revisions` is the case that fails if the
    ignore list is dropped, and
    `test_forgetting_the_provenance_columns_reports_the_whole_table` runs that
    mutation deliberately and pins what it returns.
    - **This replaced `lake_matches_warehouse`, whose lesson is worth keeping
      after the check itself is gone.** It compared three numbers (row count,
      min year, max year) and its first fixture only reached one: dropping a
      partition moves the count *and* the span together, so a mutation comparing
      only `count(*)` still went red and the span half was never measured. One
      fixture moving both ends at once cannot tell you which end is guarded —
      the same lesson as the FX partitioning fixture, and the reason the
      lakehouse cases separate "nothing changed" from "one row changed" instead
      of testing a single mixed load.

- **A determinism fix invalidates the evidence gathered for it.** Everything
  measured while diagnosing `fct_retail_returns`' tied `asof` came from a model
  that gave a different answer every build, and those figures were then quoted
  in the commit that fixed it — six numbers wrong, replicated across four files.
  After stabilising anything, re-measure the whole investigation rather than the
  figures you happen to doubt. The tell was internal inconsistency, not
  implausibility: two mutations that move the identical row set were written
  down with different deltas.
- **A count cited in prose is an untested assertion, and this repo produced
  about forty stale ones** — adding one data test once fixed a total in two files
  and left it wrong in fourteen, with `lint`, `pytest` and `dbt build` green. So
  counts of what dbt builds stay out of prose, and `tests/test_documented_counts.py`
  enforces it: the prose says "every data test on the model", and two figures
  are kept and checked against the manifest — the project's test totals (the
  README headline) and the course's `PASS=` verdicts. A per-model test count, a mart-model count, an additivity
  label count or a contracted-column count fails, naming the phrasing to use.
  - **Per-model counts went because nothing prints them.** `dbt build --select
    <model>` reports the model node and anything eagerly selected, so five of
    them were written down one or three high; the true one is the manifest's,
    filtered on `attached_node`, which no reader checks by hand.
  - **Describe a stale claim, never quote it.** The scanner cannot tell a
    quotation from an assertion, so a sentence about a count spells it `N`.
  - **Scan whole-file, never line by line.** The docs are hard-wrapped and a
    claim straddles the wrap ("10 unit" / "tests."); a per-line scan once missed
    3 of 31 claims.
  - **Number words run from ten to ninety-nine, generated rather than listed.**
    A hand-written list that stopped at "twenty" let a hyphenated total through
    in three places; below ten the words are always local ("two unit tests").
  - **A partner number is unguarded.** In "N of the M tests" the scanner reads M
    only, and the partner went stale in two files while the total was kept
    right. Write "all but two tests", which has nothing to go stale.
  - The test-count scan asserts it matched something, since the README
    headline always states one: a scanner whose pattern stops matching passes by
    not looking. The other three scans expect nothing, so they have no floor.


- **A markdown anchor is a citation nothing was checking, and the split of
  AGENTS.md (then `CLAUDE.md`) is what breaks them.** `docs/WAREHOUSE.md`
  linked at `CLAUDE.md#cbam-exposure-…` for three days after that heading became the
  `compliance-models` skill. The two citation tests could not see it: they check
  backticked *paths* and `just` recipes, and the path in the `](…)` link was
  still correct — it was the `#fragment` after it that named a heading no longer
  there. `test_every_cross_file_anchor_resolves` slugs every heading in the
  target file the way GitHub does (lowercase, drop anything outside
  `[a-z0-9 _-]`, spaces to hyphens, which is why `## The lakehouse
  (`lake/lakehouse.py`)` anchors as `#the-lakehouse-lakelakehousepy`).
  - **The vacuity guard is non-emptiness and deliberately not a count.** There
    are four anchors in the tree and deleting one is a normal edit, so a floor
    would go red on a correct change.
  - **A second assertion was written, mutated, and dropped.** It required at
    least one `../`-relative anchor, on the theory that only that form exercises
    the `doc.parent / target` join. Resolving from the repo root instead fails
    the *resolution* test with "(no such file)" on both `docs/` links, so the
    second assertion was never the thing catching it — and it would fire on a
    tree whose anchors all sat in the root, which is a correct state. **A guard
    whose effect another test already has is not evidence either way**, which is
    this repo's own rule about mutations, applied to a guard.
  - **Writing it turned up a name collision, not a typo.** `tests/test_course.py`
    already binds `_HEADING` to `^## .*$` for the structural checks. A second
    `_HEADING` with a capture group, defined above it, was silently overwritten,
    and `m.group(1)` then raised `IndexError: no such group` from a pattern that
    has none. It is `_ANY_HEADING` now.

## The offline fixtures (`tests/fixtures/ingest/`, `INGEST_FIXTURES=1`)

- **Fixtures filter rows, never columns.** Column-trimming would let a renamed
  upstream field pass CI against a fixture that matches a `stg_` model no longer
  matching reality. The OWID fixtures are gzipped CSV, not Parquet, so they still
  go through `pl.read_csv(..., infer_schema_length=None)`.
- **Three fixtures aren't trimmed at all**, each for a different reason:
  `wb_country` because it *is* the dimension the overrides seed is diffed
  against, `eu_elec_prices` because a JSON-stat grid can't be subset without
  rebuilding its index, and `ecb_fx_rates` because the interesting structure is
  *when each currency starts and stops* — cutting the date range would take the
  euro changeovers, the rouble and Iceland's nine-year gap out of CI, which are
  the four shapes the FX models exist to handle. It is gzipped (3.6 MB → 843 kB),
  and it is why `_get_json` has a `.gz` branch.
- **`fixtures.path_for()` raises on an unmapped URL** rather than falling back to
  the network — otherwise "offline CI" quietly becomes "CI that's online
  sometimes". `tests/test_fixtures.py` asserts every URL the pipeline can build
  resolves to a file that exists.
- **`_ROUTES` is an ordered dispatch table, so a route can be shadowed in
  silence.** `_fixtures.resolve` returns at the *first* pattern that matches, and
  a route added after one that already covers its URLs is never reached — the
  fixture it names is recorded, committed and never served. "Every URL resolves
  to a file that exists" stays green throughout, because the URL does resolve,
  just not to the intended route. Four checks close the loop over the three
  hand-maintained collections (`_ROUTES`, `ALL_URLS`, the files on disk), with
  the dlt source as the outer authority: no two routes claim one URL, every route
  is reachable, no recorded fixture is orphaned, every resource has a route.
  - **Reachability is the one that earns its place, and both existing tests were
    blind to it by construction.** They iterate `ALL_URLS`, so a source missing
    from that list is missing from them too — `ecb_fx_rates` was therefore
    outside every fixture test for the whole life of the FX source, its route
    never once exercised, with nothing red. `ALL_URLS` now carries **both**
    branches of `fx_start_date` (whole series on a first load, lookback window
    after), since they build different URLs onto the same route.
  - **A fixture is named after the resource it feeds, and the one exception is
    declared** (`FIXTURE_NAME_EXCEPTIONS`): the retail fixture is a real zip
    standing in for a real download, so it keeps the upstream archive's name. A
    full resource-to-fixture map would just be a fourth collection to drift.
  - **Nothing checks the recorder against the routes, on purpose.**
    `scripts/record_fixtures.py` writes through `path_for()` itself, so the two
    cannot disagree; a test there would assert what the code makes impossible.
- **dlt wraps anything a resource generator raises** in `ResourceExtractionError`,
  so tests asserting on ingest errors match that, not the underlying exception.
- **The retail workbook cache is keyed on the archive's content.**
  `retail_workbook()` unzips a 45 MB workbook into `data/cache/{fixtures,live}/`.
  Keyed on the directory alone, nothing could notice that the zip *underneath*
  had changed, so `just record-fixtures` rewriting `retail_online_retail_ii.zip`
  would leave the previous slice in place and every fixture test passing against
  data the repo no longer contains — a re-recording that looks like a no-op is
  the worst possible shape for this. A sha256 prefix in the path makes a re-record a cache
  miss. This is the same failure as the `fixtures`/`live` split one level in, and
  the same family as the `_fixtures` dlt pipeline-name suffix: a cache whose key
  doesn't include everything the value depends on. Stale digest directories are
  left rather than pruned — the cache is gitignored and safe to delete.
- **Adding a WDI indicator means re-recording** (`just record-fixtures`), on top
  of the two places listed above.

## Traps in the test suite itself

- **No routine command evaluates an asset check body.** `just test-pipeline` runs
  the modules in shell order and never executes a job;
  `tests/test_asset_checks.py` calls the bodies directly. **Patching the database
  under a check proves its logic, never its wiring**: a check kept reading the
  warehouse for `raw` after `raw` moved into DuckLake, and its tests passed
  against a fixture that had the table. Assert a count as well as the verdict,
  since the wrong source can get the verdict right by accident.
- **A test file that skips itself in CI's first step runs nowhere unless the
  second names it.** `ci.yml` runs pytest before `dbt parse`, so the five
  manifest-gated files skip there and a later step re-runs them by name;
  `tests/test_workflows.py` compares the two sets both ways. Its detector is
  anchored at column 0, because a guard that reads source as text finds its own
  strings.
- **A yml `description:` is prose**, and `tests/test_documented_counts.py` scans
  every `dbt/models/**/_*.yml` for counts of tests, mart models and the contract. The other numeric claims
  there — row counts, shares — are unguarded, because checking them needs the
  full warehouse, which CI lacks. A stale claim can *move* rather than expire —
  Antarctica's null region left the facts and survives in
  `fct_co2_estimate_versions` — and a figure covering two models is written as
  two numbers, never as a total no model has.
- **Scoring against an external rubric finds counts nothing else counted** —
  `docs/FOR_REVIEWERS.md` §6. It is also where access governance scores
  *Absent*: dbt's `access` governs who may build on a model, not who may read it,
  and DuckDB has no grants.
- **A fixture run leaks through any state it does not override.**
  `just test-pipeline` overrides `WAREHOUSE_PATH` (or it overwrites the real
  warehouse with the 17-country slice), `LAKEHOUSE_DIR` (or it merges the slice
  into the real landing zone and its weather archive) and dbt's artifact paths —
  `DBT_TARGET_PATH`, `DBT_MANIFEST_PATH`, `DBT_RUN_RESULTS_PATH` and
  `--target-path` — or the next `just pipeline-status` files the fixture's
  timings in the real build history. With the landing zone off the disk it also
  overrides `LAKEHOUSE_DATA_PATH`, which outranks `LAKEHOUSE_DIR`, and
  `LAKEHOUSE_METADATA_SCHEMA`, which is the only thing separating two lakehouses
  inside one Postgres database — the catalog URL itself is *not* overridden,
  because the fixture run keeps the database and takes a schema of its own.
  `tests/test_workflows.py` holds all six, because each is invisible when
  missing: the fixture run passes and the *next* command is the one that is
  wrong.
  - **The list of variables that outrank `LAKEHOUSE_DIR` is one tuple**,
    `lake.lakehouse.REMOTE_ENV_VARS`, read by the release's refusal,
    `tests/conftest.py` and the course guard. A variable added to the module and
    not to the tuple is missing from all three at once, and each of them fails
    silently.
  - **The course recipes are held too, by rule rather than by list.**
    A course recipe that misses the lakehouse or dbt's artifact paths writes
    into the real ones with a green build, so
    `test_every_course_recipe_keeps_the_sandbox_to_itself` derives what each
    `course-*` recipe must point into `data/course/` from the commands it runs
    (`authoring-course-modules` has the measurements).
  - **A live run isolated by hand needs two more, and no recipe or test holds
    either.** Nothing isolates `just ingest` or `just materialize`, so pointing
    one at a scratch warehouse means exporting the five above yourself, plus:
    - `DLT_DATA_DIR`, because dlt keys state on the pipeline name, not the
      destination, and a live run cannot take the `_fixtures` suffix. This is the
      trap `build_pipeline()`'s docstring gives as the suffix's reason, in
      reverse: the real `~/.dlt` watermarks would load only WDI's and FX's
      lookback windows into the empty landing zone, and the run would rewrite
      the real pipeline's state. Weather escapes, since its watermark is read
      from the destination.
    - `DAGSTER_HOME`, or the scratch runs are filed in `.dagster/`'s real run
      and asset history. Copy `.dagster/dagster.yaml` in, or telemetry is back on.
  - **Two readers ignore the variables entirely, and both would pass.** Read from
    the code, not reproduced. dagster-dbt's `DbtProject` loads the graph from
    `dbt/target/manifest.json` whatever `DBT_TARGET_PATH` says, which is only
    safe while the run's `dbt parse` would write the same manifest. And
    `publish/build_report.py` passes Evidence no warehouse path, so a redirected
    `just materialize-site` builds the site from the repo's own
    `data/warehouse.duckdb`; the override is `EVIDENCE_SOURCE__warehouse__filename`,
    relative (`building-evidence-reports`).
  - **Prove the isolation with the real files, not the exit code.** Record
    `find … -printf '%T@ %s %p'` over the real warehouse, catalog, dlt state,
    `dbt/target/` and `.dagster/storage` before, and diff after. Measured:
    `just ingest`, `just test-pipeline` and `just materialize`
    against a SeaweedFS bucket left all of them unchanged.
- **`WAREHOUSE_PATH` must be absolute**: dbt resolves paths from `dbt/`, the
  Python layers from the repo root.

## The two instance configs, the image and the compose file

`tests/test_dagster_instance.py` is the guard over the container stack, and
almost all of it is one shape: **a file that asserts something about another
file.** Dagster has no include directive and `dagster_docker` launches run
containers outside compose, so four files have to agree by hand.

- `.dagster/dagster.yaml` vs `deploy/dagster.yaml` — the shared blocks
  (`telemetry`, `concurrency`, `retention`) compared by parsed value, plus
  `max_concurrent_runs: 1` asserted on its own so both dropping to Dagster's
  default of ten together still fails.
- `deploy/dagster.yaml` vs `compose.yaml` and the `Dockerfile` — every
  `{env: X}` and every bare `env_vars` name assigned somewhere; every
  `container_kwargs.volumes` source declared as a compose volume `name:` and
  mounted at the same path in the `dagster` service; the launcher's `network`
  equal to `networks.default.name`; the launcher being
  `ServiceImageDockerRunLauncher`, with no `image` config, no
  `DAGSTER_CURRENT_IMAGE` and no `hostname:` on the service. The subclass's
  behaviour, and the private `_get_docker_image` it overrides, are
  `tests/test_docker_launcher.py`, which skips without the `deploy` group, so
  `ci.yml`'s unit-test job syncs it.
- **Why a test and not a startup check**: an unset `env_vars` name raises inside
  the *daemon*, dequeuing the run, after the UI has already reported it
  launched. A wrong volume path raises nothing at all — the run writes a
  warehouse nobody reads.
- **The pinned-tag test takes a different minimum per file**, and the docstring
  says why: the Dockerfile's images are language runtimes where `X.Y` is a
  moving alias, while `postgres:17.11` and `chrislusf/seaweedfs:4.47` are exact
  at two components. What it cannot catch — a two-part compose tag that upstream
  publishes as an alias — is written down as a deliberate limit, because nothing
  in a tag string says whether it moves.

**A guard that reads a file must skip that file's comments.** `ci.yml`'s
manifest-gated check scanned for `uv run pytest <args>` across the whole
workflow, so a comment in the `container` job explaining *why that job runs no
such command* was parsed as one, and its prose words became expected test
filenames. Fixed by dropping comment lines before the scan. The
general form: a guard a sentence about it can break is a guard that discourages
writing the sentence — the same defect PR #68 fixed in the course guard.
