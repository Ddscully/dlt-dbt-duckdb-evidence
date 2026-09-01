---
name: the-lakehouse
description: The DuckLake landing zone under data/lakehouse/ — why the change feed is useless behind dlt and what replaces it, reading table versions out of the catalog database, the absolute-vs-relative data_path that decides portability, publishing the catalog as lakehouse.tar.gz with an allowlist, the unpinnable extension and the spec-version guard. Use when editing lake/lakehouse.py, changing what the landing zone holds or publishes, debugging a DATA_PATH mismatch, or migrating a tree that predates the move.
---

# The DuckLake landing zone (`lake/lakehouse.py`)

dlt lands `raw` in a DuckLake catalog under `data/lakehouse/`; the DuckDB file
holds only what dbt builds. `just lakehouse` reports the catalog, `just ingest`
fills it, and `just sql` attaches it. The one-liners that must not depend on this
skill loading are in `CLAUDE.md`'s *The lakehouse* section — this file is the
rest.

**The hive archive is gone with it** — `archive.py` under `lake/`, `data/lake/`,
`ARCHIVED_TABLES`, the `parquet_archive` asset and `lake_matches_warehouse`.
It was a second copy of the warehouse written by hand, and DuckLake writes the
same Parquet with a catalog on top. Two of its lessons died with it and are worth
knowing were once true: the archive's output was byte-identical run to run (so a
diff of the *files* was meaningful), and its 275 partitions averaging ~47 kB were
the repo's worked example of partitions far too small for a real lake. Neither
survives a format that content-addresses its files and prunes on statistics.

## What the catalog can and cannot tell you

- **dlt's merge destroys DuckLake's change feed, and this is the finding the
  layer is built around.** `ducklake_table_changes()` returns
  `insert`/`delete`/`update_preimage`/`update_postimage` at row grain and is the
  obvious way to ask what moved. Behind dlt it answers the same thing for
  everything: reloading 500 *identical* rows through `write_disposition="merge"`
  reports **500 preimages and 500 postimages**, because dlt regenerates `_dlt_id`
  *and* `_dlt_load_id` on every row it touches. The feed is faithful; the writer
  is what makes it useless. Measured with three loads — first, identical, one-row
  change — and the second and third are indistinguishable.
  - **The replacement is a snapshot diff, and it is better in two ways.**
    `revisions()` compares the table at two versions with `EXCEPT`, projecting
    dlt's provenance columns away: **0 rows** for the identical reload, **1 row**
    for the one-row change, naming it. It works between *any* two snapshots
    rather than adjacent ones, and it is a query a consumer can re-derive from a
    published catalog months later with no bookkeeping this repo has to keep
    right.
  - **The failure mode is a plausible number, not an exception.** Drop a column
    from the ignore list and every row differs on `_dlt_id` alone, so the diff
    returns the whole table — which reads exactly like a catastrophic upstream
    restatement. `weather_revisions_are_derivable` is bounded on the total for
    that reason, and `tests/test_lakehouse.py` asserts the zero as hard as the
    one.
- **`table_versions` reads the catalog database, because the query surface
  cannot answer the question.** A dlt load writes several snapshots (staging,
  merge, cleanup), so "the previous snapshot" is usually not the previous
  snapshot *of this table*. `snapshots()` returns a `changes` map keyed on table
  *ids*; `table_changes(name, from, to)` raises `Table … does not exist at
  version N` for a range starting before the table did — it needs the answer as
  its argument. DuckLake attaches its own catalog as
  `__ducklake_metadata_<alias>`, so `ducklake_data_file` is readable with no
  second ATTACH (which DuckDB refuses outright: *unique file handle conflict*).
  - **Inlined rows have to be unioned in, and missing them fails silently.**
    DuckLake writes a change of `data_inlining_row_limit` rows or fewer (default
    10) into the catalog rather than to Parquet, leaving no `ducklake_data_file`
    row at all. A version list built from files alone skips that load rather than
    erroring. Not a test-only case: a quiet FX day is under ten rows.
- **`read_parquet` over `data/lakehouse/data/` is not the table**, in either
  configuration. At the default inlining limit small changes live in the catalog
  and the files return superseded values; at 0 they reach Parquet but so does a
  `…-delete.parquet` of `(file_path, pos)`, which makes a glob fail on the schema
  mismatch or — excluded by name — return **both** versions of the row.
- **The catalog stores its `data_path` as given, and that decides portability.**
  Absolute (what the *working* copy holds) means a consumer needs
  `ATTACH … (OVERRIDE_DATA_PATH true)` or a one-row rewrite of
  `ducklake_metadata`; relative is what the published copy holds and what a bare
  `ATTACH` needs. Absolute is right for the working copy because dlt reads it
  from the repo root and dbt from `dbt/`, which no relative path serves; the form
  is rewritten at each boundary (`publish` out, `restore` in).
  - **A relative `data_path` resolves against the catalog *file*, not the process
    working directory** — measured both ways, from the unpacked directory and
    from its parent, and the bare `ATTACH` reads the same rows from either. That
    is stronger than "relocatable" and is what lets the consumer instruction be
    one line with no `cd` in it. `tests/test_export.py` pins it.

- **`just sql` attaches the lakehouse, and without it a third of the warehouse
  does not open.** The nine `staging` models are *views* over `lakehouse.raw`, so
  a bare `duckdb data/warehouse.duckdb` binds the 26 `marts`/`analytics`/`history`
  relations and fails every one of the 9 staging views with `Catalog "lakehouse"
  does not exist!`. This is the `Catalog "warehouse" does not exist` trap from the
  other side, and the export already solves its own half by materialising staging
  (`solidify_staging`) — which is exactly what an interactive session cannot do.
  The recipe attaches in the same mode as the warehouse, so `just sql write` can
  repair a landing table and the default cannot touch one by accident.

## Migrating, and the empty-file trap

- **A tree that predates the move has to be migrated, and nothing says so.** A
  fresh clone cold-starts and a release restore carries the tarball, but a
  working tree that already held `raw` inside `data/warehouse.duckdb` gets
  neither: dbt reads `lakehouse.raw`, the catalog is empty, and the weather
  watermark reads null — so the next ingest cold-starts at
  `WEATHER_COLD_START_YEARS` and *silently* ignores however many years the old
  file holds. Nothing is lost and nothing goes red; the archive is simply in the
  wrong file. Carrying `raw.om_weather_daily` across with its
  `_dlt_load_id`/`_dlt_id` is the whole of the fix, and it is the same shape as
  `restore` — dlt finds no `_dlt_version`, calls the dataset new, and merges onto
  the carried rows.
  - **dbt's `ATTACH IF NOT EXISTS` leaves an empty DuckDB file** at the catalog
    path on any build that runs before the first ingest, and a read-only attach
    of *that* fails with `Existing DuckLake … does not exist - and creating a new
    DuckLake is explicitly disabled`. So "is there a lakehouse here" is
    `is_catalog()` — does it hold `ducklake_metadata` — not `path.exists()`.
  - **The warm-state refusal moved with the landing zone and is unchanged in
    substance.** dlt with local state against a restored destination still dies
    with `Table with name _dlt_version does not exist!`; re-measured against the
    directory copy rather than inherited from the table carry, because a new
    mechanism does not escape an old trap by being new.



## Publishing the landing zone

- **The landing zone is published as a second release asset**, `lakehouse.tar.gz`,
  and that is what keeps the weather archive deepening instead of cold-starting
  every month. `CARRIED` names `history` alone now — the weather rows are not in
  the database to carry — so `publish/restore_history.py` restores the tarball
  beside it and one command still carries both. Verified end to end: restore a
  release into an empty tree and `weather_watermark()` reads 2022-12-31, so the
  next ingest asks for a 90-day lookback rather than three years.
  - **What ships is an allowlist (`PUBLISHED_TABLES`), for two reasons that would
    each be sufficient.** Cost: only the weather archive is unreproducible within
    the budget; everything else in `raw` is a free re-fetch. Disclosure:
    `raw.retail_invoice_lines` and dlt's `raw_staging` copy hold 824,364 clear
    customer ids between them, and shipping `raw` whole would undo the largest
    privacy gain of the move.
  - **And it cannot be fixed by dropping afterwards, which is why the catalog is
    *built* from the list.** DuckLake keeps dropped tables in earlier snapshots:
    after `drop table`, `select * from lh.raw.secret at (version => 2)` returned
    the row — measured, with a customer id in it. A published catalog contains
    what it was created with, permanently.
  - **The published catalog's `data_path` is relative and the working one's is
    absolute**, and the form is rewritten at each boundary (`publish` out,
    `restore` in). There is no form that serves both: a consumer needs relative
    to open it with a bare `ATTACH` wherever they unpacked it, and the working
    copy needs absolute because dlt reads it from the repo root and dbt from
    `dbt/`. DuckLake checks the stored path on every attach and refuses a
    mismatch, so getting this wrong fails loudly on the *next* command.
  - **`just` exports an absolute `LAKEHOUSE_DIR` for every recipe**, which is not
    a convenience. `profiles.yml`'s relative default made a `dbt build` create a
    catalog recording `../data/lakehouse/data/`, which `lake.lakehouse` then
    could not open — and the error names a path nobody typed. `WAREHOUSE_PATH`
    gets away with a relative default because a plain file keeps no such record.
    - **The workflows go through `just` now, and this is the defect that made
      them.** Until 2026-09-01 they ran `uv run dagster job execute` directly and
      each set the paths itself — so all four needed the same new line when the
      landing zone moved into DuckLake, and none of them got it. dlt writes the
      catalog from the repo root and records an absolute `data_path`; dbt then
      resolves `profiles.yml`'s relative default from `dbt/`, and DuckLake
      compares the two **as strings** — so *the same directory under two
      spellings* is refused with `DATA_PATH parameter
      "../data/lakehouse/data/" does not match existing data path`. The failure
      is in `dbt build`, one layer downstream of the layer that chose the
      spelling, and **no recipe could reproduce it because every recipe exported
      the variable that hid it**: a faithful run had to unset `LAKEHOUSE_DIR` and
      work in a clone.
      - **`.github/actions/setup` is the one definition now** — uv, the venv,
        `just`, and all three paths absolute. `WAREHOUSE_PATH` was previously set
        in `ci.yml` alone; it is set everywhere, which is a no-op in value and
        removes a `paths.py` fallback from the question. Three tests in
        `tests/test_workflows.py` hold it: the action must export all three (the
        vacuity guard — the other two assert an *absence* and would both pass if
        nothing set them at all), no workflow may define one itself, and every
        workflow running the pipeline must use the action.
      - **`just` respects a pre-set `LAKEHOUSE_DIR` and used to override
        `DAGSTER_HOME`.** `env("LAKEHOUSE_DIR", …)` against a bare assignment —
        the two agreed in CI, since `$GITHUB_WORKSPACE` *is* `justfile_directory()`
        there, which is exactly what would have made a disagreement invisible
        until the day they differed. Both take `env()` now.
  - **A tarball rather than loose files**, because a GitHub release asset is a
    file and a DuckLake is a directory. One asset also means one line in
    `SHA256SUMS`, so `sha256sum -c` still covers the whole release.
  - **`SHA256SUMS` is now produced by walking the export directory** rather than
    by listing what the code thinks it wrote. The two were identical while the
    release was a database and some Parquet; the first artifact written by a hook
    would have shipped unverified with nothing to say so.
  - **The exporter takes `lakehouse_dir` as a parameter, and defaulting it made a
    test pass for the wrong reason.** `publish_lakehouse` read the module
    constant, so the *shape of the export* depended on whether the machine had
    ingested: an empty `data/lakehouse/` gave five `SHA256SUMS` lines and a
    populated one gave six, and `test_sha256sums_is_checkable` asserted five. It
    was green at commit time because the lakehouse happened to be empty, and it
    would have stayed green in CI forever — CI builds from nothing. It only fails
    on the machine of anyone who has run `just ingest` once. The fixture names an
    empty directory now instead of inheriting one.
  - **The second release asset had no test at all**, which is how the above
    survived. It is a *published artifact*, so it earns three on this repo's own
    terms, and each was confirmed by mutation: the tarball is checksummed (walk
    the directory, not the list); the published catalog opens with a **bare**
    `ATTACH` from any working directory; and a table outside `PUBLISHED_TABLES`
    is absent **at every snapshot**, not merely the current one — which is the
    assertion that matters, because time travel is what makes
    copy-then-drop useless as a mitigation.
  - **The DuckLake extension is the one version in this stack that nothing can
    pin, and it is not the coupling people expect.** It cannot drift against
    `duckdb`: builds are served per DuckDB version and one built for another
    refuses to load, naming both versions. What is unpinnable is the extension
    *itself* — it is a 36 MB binary from extensions.duckdb.org with no PyPI
    package, and `duckdb_extensions()` reports its version as a git hash
    (`d8a1881e`) where `parquet` reports `v1.5.5`. That is the "unpinned binary
    no lockfile here can see" shape that lost pyright to ty and keeps `uvx dg`
    out, except here there is no lockfile-visible alternative to choose.
    - **`just setup` installs it, and that is a download location rather than a
      pin.** DuckDB autoloads it on first use, so the lake works without this;
      what it buys is that a network failure surfaces at setup instead of inside
      a `dbt build` in a Dagster op. The version is right by construction — the
      extension directory is keyed on the DuckDB version, so a bump re-fetches
      rather than going stale, and `duckdb-cli` reads the same directory, so one
      install serves `just sql`. Plain `install` and not `force install`: the
      only case the no-op misses is a rebuild republished for an unchanged
      DuckDB version.
    - **So the catalog's own spec version is guarded instead** —
      `ducklake_metadata` holds `version` (`1.0` today) and `created_by`, the
      direct analogue of `storage_version` and `duckdb_version`, and both now
      ship in `manifest.json` and the release notes. See *Publishing* for the
      shape, which is the storage-version guard's with one part working harder.
