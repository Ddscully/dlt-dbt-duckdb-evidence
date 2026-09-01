---
name: publishing-a-release
description: The publication boundary — publish/export_warehouse.py and publish/restore_history.py. The storage-format ceiling and the two moments its tripwire fires, the DuckLake spec ceiling that has no PR to fail, reading data_loaded_at from the catalog rather than the copy, and carrying the unreproducible tables forward so the snapshot accumulates and the weather archive deepens. Use when editing anything under publish/, changing release-data.yml or pages.yml, or reasoning about what a consumer of a data-YYYY-MM-DD release can open.
---

# The publication boundary (`publish/`)

`just export-data` packages the built warehouse into `data/export/`;
`.github/workflows/release-data.yml` runs it monthly against live sources and
attaches the result to a dated `data-YYYY-MM-DD` GitHub release.
`just restore-history` carries the previous release's unreproducible tables in
before the graph runs.

**This is the only moment data crosses from a machine it is on to a machine it is
not**, which is why the personal-data policy, the storage ceiling and the
attribution obligation are all applied here rather than in a model. The
constraints that must not depend on this skill loading are in `CLAUDE.md`'s
*Publishing* section; this file is the rest.

## What the manifest tells a consumer they cannot see

- **`manifest.json` carries an `additivity` map, and it exists because a Parquet
  file has types and nothing else.** 280 published columns are labelled
  `additive` / `semi_additive` / `non_additive` / `not_a_measure`, and about half
  are non-additive — `sum(renewables_share_pct)` is nonsense that returns a
  number, with no error anywhere for a consumer who cannot be paged.
  `export_warehouse.additivity()` reads the labels off `meta:` in the dbt
  manifest through the `extra_manifest` hook, so nothing in
  `modern_data_stack.export` had to learn about them.
  - **It degrades to `None`, not to `{}`, when `dbt/target/manifest.json` is
    absent**, and the distinction is load-bearing: `None` says nobody asked dbt,
    `{}` would say dbt was asked and knows of no labelled column. Only the first
    can be true by accident.
    - **This is where it parts company with `classifications()`, which degrades
      to `EXTRA_CLASSIFICATIONS` instead.** That one may publish a partial
      answer because a partial answer still masks every identifier it names —
      it fails *safe*. A partial additivity map fails the other way: a consumer
      seeing `analytics` labelled and `marts` missing cannot read the gap as
      "nobody asked" rather than "nothing to say", and the default reading of a
      missing label is the wrong one. So `EXTRA_ADDITIVITY` is dropped on that
      path too.
  - **The 56 `analytics` labels are `EXTRA_ADDITIVITY`, and 37 of them are
    copies of the mart's.** `analytics.co2_intensity` is
    `select * from marts.fct_emissions_energy` plus `co2_per_gdp_const_usd` and
    `co2_intensity_rank`, so its labels *are* the mart's — and they are stated
    rather than inherited at runtime because inheriting fails open: rename a
    column in the mart and a derived copy loses its label with nothing to say
    so. `test_a_copied_column_keeps_the_label_the_mart_gave_it` compares the
    column set as well as the values, and was mutation-proven from both sides.
    `analytics.retail_rfm` cannot be matched by name at all — `frequency` is
    `dim_retail_customer.n_orders` and `monetary_gbp` is its `net_revenue_gbp`,
    the same renaming that makes `EXTRA_CLASSIFICATIONS` name `monetary_gbp` by
    hand — so its coverage is asserted against the frame `build_retail_rfm`
    emits rather than against a list.
  - **Keyed by `schema.alias`**, so it names the relations the release actually
    ships. The versioned model appears twice — `marts.fct_emissions_energy` and
    `marts.fct_emissions_energy_v1` — which is what the Parquet files are called,
    and v1 inherits its 36 labels through `include: all`.

## Reading the load time

- **`data_loaded_at` has to name the catalog, and getting it wrong is silent in
  two directions.** `loaded_at` read an unqualified `raw._dlt_loads` off the
  copy, which stopped being where dlt writes when the landing zone moved into
  DuckLake. On a fresh or CI tree that raises, the `except duckdb.Error` returns
  `None`, and every release body says *"Data last landed: unknown."* On a tree
  migrated in place — still holding the pre-move `raw` — it returns the stale
  copy's timestamp instead: measured 4h20m adrift here, and a believable wrong
  answer is the worse of the two. `transform/pipeline_status.py` was migrated for
  exactly this and documents the hazard; `export.py` was missed, and the test
  fixture hid it by building `raw._dlt_loads` *inside* the warehouse, the shape
  production no longer produces.
  - **The reader is a project hook, not a package parameter.** `export()` takes
    `read_loaded_at` the way it already takes `extra_artifacts` for the tarball,
    so the package still knows nothing about DuckLake; `publish/export_warehouse.py`
    supplies `landed_at`, which attaches read-only **only when there is a
    catalog** — the same test `solidify_staging` makes, so a warehouse carrying
    its own `raw` is still exportable. Where both exist the catalog wins.
  - **The fixture now holds both tables with different timestamps**, because
    `assert data_loaded_at is not None` passes on a clock read, a stale read and
    the wrong catalog's read — every way of being wrong except the one that
    raises. Asserting the value is what makes the mutation red.

## The two format ceilings

- **The published file's storage format is not its writer's version, and the
  manifest carries both.** `duckdb_version` answers "who wrote this";
  `storage_version` answers "can I open it", which is the only question a
  consumer has. They differ: DuckDB 1.x writes format **64** by default — the
  one `v0.10.0` through `v1.1.3` all read — so a file written by 1.5.5 opens on
  a client five years older. The release notes said "Written by DuckDB 1.5.5.
  Older clients may not read the storage format" for the whole life of the
  release, which was unmeasured and, it turns out, *pessimistic*.
  - **There is no SQL that answers it.** `duckdb_databases()` returns an empty
    `options` map, there is no pragma, and the only surface DuckDB exposes is
    `ATTACH … (STORAGE_VERSION …)` on the write side.
    `modern_data_stack.export.storage_version` reads the header instead — 8-byte
    checksum, `DUCK` magic, little-endian uint64 — which is the right shape for
    a release gate anyway: it describes the artifact, not the process. The
    mapping, measured by writing a file per version on 1.5.5: **64** =
    v0.10.0–v1.1.3, **65** = v1.2.x, **66** = v1.3.x, **67** = v1.4.x, **68** =
    v1.5.x. A *lower* number is the more widely readable file.
  - **The guard is split across two moments because one of them cannot see the
    thing that moves.** DuckDB 2.0 ships a new default storage format, nothing
    caps `duckdb>=1.1`, and `lockfile-only` means the bump arrives as one line
    of a grouped monthly Dependabot PR. Every *artifact* check would pass it —
    they read a file the same binary just wrote. So
    `test_the_installed_duckdb_still_writes_the_format_the_release_promises`
    checks the **toolchain** (a bare `duckdb.connect()`, no `STORAGE_VERSION`)
    and is the only one that fires on the bump, on the PR, before it merges. The
    artifact checks — `export()`'s ceiling and `release-data.yml`'s verify step
    — catch a file that should not be uploaded. This is "green CI proves nothing
    about versions" one turn further round: what moves is the *format of the
    artifact* rather than the code.
  - **`max_storage_version` has no default in the package**, matching `schema`
    on `db.write_frames`. A ceiling is a compatibility promise to consumers the
    package knows nothing about, and it is only meaningful beside the minimum
    reader version a project states — `MAX_PUBLISHED_STORAGE_VERSION` and
    `MIN_READER_VERSION` live in `publish/export_warehouse.py` together.
  - **The ceiling is tested from both sides**, because `>` against `>=` is the
    slip and a one-sided test passes under either — the same lesson as
    the export ceiling's two sides and the FX partitioning fixture.
  - **Not an upper bound on `duckdb`, deliberately.** Pinning would block every
    unrelated fix in 2.x to guard one property; the tripwire lets the bump land
    and makes a person decide the format question with a red test naming it.
    `dagster<3.15` therefore remains the only hard upper bound in the tree.
- **The landing zone has its own format version, and its tripwire has to work
  harder than the file's.** `ducklake_metadata.version` (`1.0`) is what decides
  whether a consumer's `ducklake` can open `lakehouse.tar.gz`, exactly as
  `storage_version` decides it for `warehouse.duckdb`;
  `MAX_PUBLISHED_LAKE_VERSION` is the ceiling and `ducklake.publish` refuses
  above it. What differs is the *moment*, and it is the whole reason this needed
  building rather than copying.
  - **There is no PR to fail.** DuckDB's format moves when `uv.lock` moves, so
    that tripwire fires on a Dependabot PR with a person already reading the
    diff. The DuckLake spec moves when extensions.duckdb.org republishes, and
    nothing in this repo changes — see the extension bullet under *The
    lakehouse*. `test_the_installed_ducklake_still_writes_the_spec_the_release_promises`
    on the next CI run is the only thing that can say so, which makes the
    toolchain half load-bearing here rather than merely thorough.
  - **A ceiling that exists is not a ceiling that is applied**, and the two need
    separate tests. `test_the_lakehouse_ceiling_is_inclusive` proves `publish`
    enforces a limit it is handed — from both sides, for the `>` against `>=`
    reason — while `test_the_release_path_applies_the_lakehouse_ceiling` drives
    `run()` with an impossible one. Deleting the constant from
    `publish_lakehouse`'s call fails only the second: the manifest assertions
    keep passing, because the spec is still under the ceiling nobody checked.
    Verified by mutation, as was each other half.
  - **`created_by` is a DuckDB git hash and answers a different question.** It
    ships beside the spec for `duckdb_version`'s reason — who wrote this, against
    can I open it — and `catalog_metadata` returns the whole map so the two are
    one read rather than two.
  - dlt's own `automatic_migration` defaults to **False**, so a catalog *we*
    write is safe by refusal if the spec ever moves under us. This guards the
    half dlt cannot see: a consumer meeting a tarball written against a spec
    their extension does not know.

## Carrying the unreproducible tables forward

- **Each release carries the previous one's unreproducible tables forward**
  (`publish/restore_history.py`), which is what makes the published snapshot
  accumulate a real revision log instead of holding one version per row forever,
  and what keeps the weather archive deepening instead of resetting to a
  three-year cold start every month. The restore runs *before* the graph, so dlt
  lands into a file that already exists — safe because dlt keys "is this
  destination fresh?" on its own bookkeeping, not on the file existing (verified:
  a fixture load into a restored file still fetched the full WDI series, not a
  five-year window).
  - **Two reasons a table is carried, and one mechanism.** `history` is state *in
    principle*: no rebuild invents a revision. `raw.om_weather_daily` is
    unreproducible within a *budget* — the archive costs more than Open-Meteo's
    10,000 units a day. Different arguments, identical consequence, so
    `modern_data_stack.history` takes a tuple of `Carry` rules (schema, optional
    table allowlist, the columns that prove the relation qualifies) and `carry`
    has **no default**, for `db.write_frames`'s reason.
  - **The allowlist is not a stylistic choice.** Copying `raw` whole would bring
    dlt's `_dlt_loads`/`_dlt_version`/`_dlt_pipeline_state` with it, and dlt would
    then believe every table the schema describes is present — the *other* merge
    resources die on `DELETE FROM "raw"."ecb_fx_rates" … does not exist`. Measured,
    along with the fact that carrying more bookkeeping makes it worse rather than
    better.
  - **Whether it works at all depends on dlt's *local* state, which is why the
    failure is invisible in CI.** A fresh runner has no `~/.dlt`, so dlt queries
    the destination, finds no `_dlt_version`, treats the dataset as new and merges
    onto the carried rows — measured at 44,936 weather rows surviving a full load.
    A machine that has run `just ingest` has state, trusts it, and dies with
    `Table with name _dlt_version does not exist!`. `sync_destination()` does not
    fix it. So `run()` **refuses** when a landing table would be carried and dlt
    has local state, naming `rm -rf ~/.dlt/pipelines/modern_data_stack` as the
    remedy — the restore is replacing the destination that state describes.
    Carrying `history` alone never creates the `raw` schema and is unaffected,
    which a test pins: the recipe that already worked has to keep working.
  - **`irreplaceable_rows()` is the one count.** `just clean warehouse`'s gate,
    the restore step's `restored_rows` and the "did not shrink" verify all call
    it, so a rule added to `CARRIED` reaches all three at once instead of leaving
    whichever was added last unguarded.
  - **Only "no previous release" may skip.** A failed download or restore is
    fatal in `release-data.yml`: continuing would publish an empty history that
    the *next* release then inherits, which is the exact failure the step
    prevents. The verify step asserts the shipped snapshot is no smaller than
    what was carried in. `pages.yml` runs the same step `continue-on-error`,
    because there the snapshot is a read-only display and a missing release
    should cost one section of one page, not the deploy.
  - **A release is two assets and a workflow that downloads one of them fails
    silently, which `pages.yml` did until 2026-08-27.** It asked for
    `warehouse.duckdb` alone; `restore_history` finds the lakehouse *beside* the
    database rather than being told where it is, so it got a directory with no
    tarball in it — which is the "restoring nothing is normal" path, not an
    error. The cost lands a layer down and is a *depth*, not a failure: every
    workflow rebuilds the marts from `raw`, `raw` is in the DuckLake catalog now,
    so the published database carries no weather rows at all —
    `weather_watermark()` reads null, the ingest cold-starts at
    `WEATHER_COLD_START_YEARS`, and `marts.fct_country_weather_year` builds three
    years deep against the release's fifteen. The Weather page then renders
    perfectly off a thin mart. Green build, green checks, right shape, wrong
    depth, and no line anywhere saying so. This is the "no workflow goes through
    `just`, so all four needed the same line" shape again, one asset further on;
    `tests/test_workflows.py` now derives which workflows restore and asserts each
    downloads every asset, with the names read from the code rather than retyped.
  - **It refuses to overwrite a destination that already holds carried state**,
    so running it against the real warehouse can't destroy months of local
    versions — `--force` if that is genuinely what you want. It also rejects a
    source relation missing the columns its rule requires, which would otherwise
    fail later and much less legibly: a snapshot without dbt's SCD2 columns dies
    inside `dbt build`, and a landing table without `_dlt_load_id`/`_dlt_id` dies
    at the next load with DuckDB's `Adding columns with constraints not yet
    supported`, dlt trying to add the column `NOT NULL` to a table with rows.
  - Verified end to end against fixtures rather than by waiting for OWID: export
    release 1, restore into a fresh warehouse, restate three country-years in
    `raw.owid_co2`, rebuild — 595 snapshot rows became 598 and
    `fct_co2_estimate_versions` showed the three at version 2.
