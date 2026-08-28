---
name: repo-guards
description: The hand-maintained lists in this repo and the tests that hold them to the tree — SOURCE_TABLES, RAW_DESCRIPTIONS, WB_WDI_INDICATORS, ATTRIBUTION, pages.yml's path allowlist, the seven asset-check bodies, counts cited in prose — plus the offline fixture dispatch table behind INGEST_FIXTURES. Use when adding or editing one of those lists, writing a guard for a duplication, re-recording fixtures, or when a citation, count, workflow-path or fixture test fails.
---

# Hand-maintained lists, their guards, and the fixtures (`tests/`)

Every list in this repo that restates something another file already knows is a
place two files can disagree in silence. None of these failures is loud: an
unlisted source yields no row and the page under-reports while looking complete,
an unlisted resource materialises with no description, a stale count reads as
authoritative. Each one below is now asserted against the authority it copies.

The two-tier test split, `WAREHOUSE_PATH`, coverage and `nightly.yml` stay in
`CLAUDE.md` under *Testing*. The mutation method these guards were written with
is in the `unit-testing-dbt-models` skill.

## The lists and what holds them

- **Two lists re-enumerated the seven dlt resources with no covering test, and
  both fail green.** `SOURCE_TABLES` (`transform/pipeline_status.py`) is
  iterated by `observability.build_sources`, so an unlisted source yields no
  row and the pipeline page under-reports while looking complete — the exact
  symptom CLAUDE.md already records from a *different* cause (the Arrow path's
  missing `_dlt_load_id`, "six sources for seven"). `RAW_DESCRIPTIONS`
  (`orchestration/assets.py`) is read with `.get(name)`, so an unlisted
  resource materialises with no description at all. Both are now held to
  `public_indicators()`, the same authority `load_groups` and
  `PARTITIONED_RESOURCES` use.
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
    nothing tying them, while the bullet under *Conventions* in `CLAUDE.md`
    invites the edit.
    **The dangerous drift is the one where both sides agree on the keys**: swap
    `NY.GDP.MKTP.CD` for `.KD` and current-dollar GDP lands in
    `gdp_constant_usd`, which every intensity figure divides by. `stg_wdi`
    carries fourteen data tests, every one an `accepted_range`, and both series
    are non-negative USD, so nothing goes red — and per the GDP bullets in
    `CLAUDE.md` that substitution flips the decarbonisation *sign* for 30 countries. A guard
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
- **The seven `@dg.asset_check` bodies are unit tested in
  `tests/test_asset_checks.py`, and were not before 2026-08-25.**
  `tests/test_definitions.py` proved each check was *registered* — that it would
  run at all. Nothing proved any of them would *notice*: the bodies were only
  ever executed by a full materialize, so `just test` could not tell a working
  check from one whose logic had inverted, and the answer arrived minutes later
  in `just test-pipeline` instead of in the `just test` loop.
  `AssetChecksDefinition` is callable and none of the seven take a `context`, so
  no execution harness is needed — point the module's `DUCKDB_PATH` (or
  `LAKE_DIR`, or `page_routes`) at a throwaway, call the check, read the
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
- **`dbt build --select <model>` does not report that model's test count, and
  five places in this repo said it did.** Its PASS total counts the model node
  itself, plus anything eagerly selected — so `stg_retail_lines` was written
  down as 22 where it has 19, and `fct_fx_rates_daily`, `fct_fx_rates_periods`,
  `fct_retail_returns` and `dim_date` were each written down as one more than
  they have. The `dim_date` one shipped in the PR that added its unit tests and
  survived until a scanner went looking. The count that means "tests attached to
  this model" is the manifest's, filtered on `attached_node`; nothing printed by
  a build is it.
- **A count cited in prose is an untested assertion, and this repo produced
  about forty stale ones.** `tests/test_documented_counts.py` scans tracked
  markdown and the two `_unit_tests.yml` headers for any integer in front of a
  test-noun and requires it to be one the manifest actually produces. It exists
  because adding a single data test moved 368 to 369 in two files and left it
  wrong in fourteen — including `README.md` labelling `docs/DATA_QUALITY.md`
  with a count of 368 while linking to a file that already said 369. `lint`,
  `pytest` and `dbt build` were green through all of it.

    The guard forbids quoting a superseded count directly in front of a
    test-noun, which is why this bullet phrases the old figures the long way
    round. That is the intended cost — an exemption comment would be a hole
    someone eventually parks a real staleness in.
  - **Scan whole-file, never line by line.** These docs are hard wrapped at ~80
    characters and the claims straddle the wraps — `docs/DATA_QUALITY.md` ends a
    line on "10 unit" and starts the next with "tests.". A per-line scan missed
    3 of 31 claims and passed a mutated unit-test count; it was a mutation that
    found that, not review.
  - **Per-model counts are scoped to their own model, and were not at first.**
    Folding them into one allowed set put `fct_retail_returns`' 10 and
    `fct_fx_rates_daily`' 14 into the *project-wide* set — and 10 had been the
    project-wide unit-test total one commit earlier, so a sentence still
    claiming that old total became legal anywhere, and the guard silently
    reopened the staleness it exists to catch. (Writing the example out here
    fails the guard, which is the bullet above demonstrating itself.) A count is now accepted only where the nearest *preceding* model
    mention owns it, which is how these documents establish context: a heading,
    then prose about that model. Adding a model to `CITED_MODELS` is therefore
    cheap — but a model whose count is cited and *not* listed is unguarded
    rather than wrong, which is the failure mode to watch.
  - **The number is not always adjacent to the noun.** `of those` / `of the` may
    sit between them (a spelled-out number, then "of those tests"), and
    CLAUDE.md writes counts as
    words. Both are handled; words only from ten up, because below that they are
    always local ("Two unit tests catch all five") and admitting them produced
    nine false positives against zero finds. Anything longer than that filler is
    deliberately out — "422 of the 424 tests" has to capture 424, not 422. What
    still escapes is a number with no test-noun after it at all ("pass all 14:"),
    so phrase a count with its noun.
  - `seen > 35` is the vacuity guard. A scanner whose patterns stop matching
    passes by not looking — the same failure `_ROUTES` reachability exists for.


- **A markdown anchor is a citation nothing was checking, and the split of
  CLAUDE.md is what breaks them.** `docs/WAREHOUSE.md` linked at
  `CLAUDE.md#cbam-exposure-…` for three days after that heading became the
  `compliance-models` skill. The two citation tests could not see it: they check
  backticked *paths* and `just` recipes, and the path in the `](…)` link was
  still correct — it was the `#fragment` after it that named a heading no longer
  there. `test_every_cross_file_anchor_resolves` slugs every heading in the
  target file the way GitHub does (lowercase, drop anything outside
  `[a-z0-9 _-]`, spaces to hyphens, which is why `## The lakehouse
  (`lake/lakehouse.py`)` anchors as `#the-lakehouse-lakelakehousepy`).
  - **The vacuity guard is non-emptiness and deliberately not a count.** There
    are four anchors in the tree and deleting one is a normal edit, so a floor
    would go red on a correct change — the opposite trade from `seen > 35` in
    `tests/test_documented_counts.py`, where claims only accumulate.
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
- **The retail workbook cache is keyed on the archive's content, and had to be.**
  `retail_workbook()` unzips a 45 MB workbook into `data/cache/{fixtures,live}/`
  and used to key that on the directory alone — if the `.xlsx` was there, it was
  returned. Nothing could then notice that the zip *underneath* it had changed,
  so `just record-fixtures` rewriting `retail_online_retail_ii.zip` left the
  previous slice in place and every fixture test went on passing against data the
  repo no longer contained — a re-recording that looks like a no-op is the worst
  possible shape for this. A sha256 prefix in the path makes a re-record a cache
  miss. This is the same failure as the `fixtures`/`live` split one level in, and
  the same family as the `_fixtures` dlt pipeline-name suffix: a cache whose key
  doesn't include everything the value depends on. Stale digest directories are
  left rather than pruned — the cache is gitignored and safe to delete.
- **Adding a WDI indicator means re-recording** (`just record-fixtures`), on top
  of the two places listed above.
