---
name: contracts-and-data-quality
description: The dbt metadata layer and its gates — data tests and dbt_utils, store_failures, groups and access, enforced contracts, per-page exposures, the meta additivity labels, the versioned fct_emissions_energy and its enforced deprecation, and the bus matrix. Use when editing dbt/models/_groups.yml, dbt/models/_exposures.yml or any marts _*.yml, adding or changing a data test, contract, access level, meta label or model version, selecting marts assets by key, or when dbt parse fails on a deprecation or access error.
---

# Data-quality gates, contracts, ownership and versions

Who owns each model, who may depend on it, what shape it promises, who reads
it, and which tests hold it — all declarative, and all enforced by something.
Unit tests are their own skill, `unit-testing-dbt-models`; the human-facing
account of the same ground is `docs/DATA_QUALITY.md`.

## Data-quality gates (`dbt/models/**/_*.yml`)

`dbt_utils` is the only dbt package, for four generic tests:
`unique_combination_of_columns` (the grain contracts), `accepted_range`,
`expression_is_true` and `equal_rowcount`. `dbt source freshness` reads dlt's
`_dlt_load_id` as a unix epoch.

- **`dbt deps` first, always.** `dbt/dbt_packages/` and the manifest in
  `dbt/target/` are gitignored, so a fresh clone needs `dbt deps` before
  `dbt build`, `dbt parse` or `sqlfluff`, and `dbt parse` before the asset graph
  will load. The recipes depend on `dbt-deps` and `dbt-parse`; `prepare_if_dev()`
  does both, but only under `dagster dev`.
- **One `unit_tests:` key per yml.** A second block parses and dbt *merges* the
  lists, warning `DuplicateYAMLKeysDeprecation` — gone in Fusion, and silent until
  then.
- **Test args go under `arguments:`, and the key is `data_tests:`.** The flat
  `tests:` form is deprecated in 1.10 and gone in Fusion.
- **Every test's failures are stored**: `+store_failures: true` project-wide, so a
  red check gives `select * from dbt_test__audit.<test_name>` for the rows.
- **Tests are calibrated to fail on bugs, not on reality.** `income_group` is
  nullable on purpose (the `country_overrides` territories have no World Bank
  classification) and `co2_per_capita` has no ceiling (small petrostates reach
  780 t/person). Check the full distribution before tightening a bound — the
  17-country fixture slice passes thresholds the full data breaks.
- **There are thirty-six unit tests, over twelve models, because a data test
  cannot see a wrong answer that is a legal one.** Change `dim_date`'s
  `fiscal_quarter` from `/3 + 1` to `/ 4` and every fiscal quarter is wrong while
  all 19 data tests on the model pass; its three unit tests fail. Which models,
  what mutating each proved, and the fixture shapes are `unit-testing-dbt-models`.
- **A unit test that mocks five inputs is telling you a model is two models.**
  The CBAM fallback rule needed a markup schedule, a country dimension and an
  empty grid table to reach through `fct_cbam_exposure`; against
  `int_cbam_default_factors` it mocks one input. Fixture size is the signal, and
  the case for each of the three `int_*` models.
- **Mutate a determinism guard repeatedly.** DuckDB's parallel asof join picks a
  different tied row per run, so the tie-break test passed a broken model 28.7%
  of the time, and single spot-checks called it stable. `unit-testing-dbt-models`
  has the fixture that brings that to 1.1%.
- **Unit tests stay inside `dbt build`.** dbt Labs' advice to exclude them is
  about warehouse spend; here they cost seconds (~5s of dbt's own time, ~11s wall
  for `just dbt-unit-test`, 2026-09-09), and a broken fiscal calendar should stop
  the release.
- **Source freshness measures our load, not the publisher's.** It is
  tautologically green in CI, so it is a recipe, not a workflow step.

## Contracts, ownership and versions (`_groups.yml`, `_exposures.yml`)

- **Groups are by domain, not layer** — `reference`, `country_stats`,
  `compliance`, `retail` — or no boundary is ever crossed.
- **Staging is `private` and marts are `public`** (set per folder), because every
  mart ships as Parquet to people who cannot be paged. The exceptions are the
  content: `stg_country` and `stg_energy` are `protected`, the only places one
  domain reads another's cleaning layer, with the reasons beside the override.
  Breaking one fails `dbt parse`, naming the consumer.
- **Contracts are enforced on every mart model — 20 relations (20 models, one of
  them versioned) and 365 columns, each with a `data_type`.** The column list was
  generated from `information_schema` and inserted line-wise. **Never round-trip
  these ymls through PyYAML**: it reflows every description to add a scalar.
  - The schema contract catches what the grain contract cannot — a column
    changing type under a consumer. Declaring `year` as `VARCHAR` fails the build
    before it writes anything. CI's 17-country slice builds the same types.
  - A contracted incremental model must set `on_schema_change`;
    `fct_fx_rates_published` uses `fail`, because a new column there needs a
    person to decide on a `--full-refresh` of 265k rows.
- **The marts ymls are one per dbt group**, the split dbt itself can check,
  because shared prose behaves like a merge lock (`AGENTS.md`, *Branches and
  PRs*). `_unit_tests.yml` stays whole: it is one axis of assertion across twelve
  models. A test that names a yml is a list that can go quiet, so the privacy test
  globs and derives the expected set from the `.sql` files. When moving yml
  blocks, compare a manifest fingerprint before and after: a green build proves
  the yml parses, not that nothing moved.
- **Exposures are per *page*** — nine Evidence pages and the release — so
  `dbt ls --select +exposure:evidence_retail` answers "what breaks" for one page.
  `tests/test_exposures.py` holds them to the SQL through
  `publish/build_report.py`'s `page_tables()`. An exposure cannot name Polars
  output, so `pipeline.md` has none (and `index.md` reads nothing); what the pages
  read that dbt cannot describe is exactly `TABLE_TO_ASSET_KEY`, asserted. The
  release exposure is exactly the marts.
- **Every numeric mart column declares `meta: {additivity: …}`** from a closed
  vocabulary — `additive`, `semi_additive`, `non_additive`, `not_a_measure` —
  because neither a type nor a test says whether `sum()` means anything: 94 of
  the 192 are non-additive. Those are manifest counts, and the ymls carry 192
  literal `additivity:` entries — the two agree only while no model version
  inherits labels through `include: all`, as `fct_emissions_energy_v1` did. `tests/test_additivity.py` holds coverage, the closed
  vocabulary, numeric-only labels, and a name rule — no ratio-named column may be
  summable — which is the one check that catches a label present and *wrong*.
  - `semi_additive` must say which direction fails, and there are 13
    `semi_additive` columns: `population` gives person-years across years;
    `original_quantity` belongs to the matched purchase, so summing it counts a
    purchase once per return matched to it. `gdp_usd` is `semi_additive` and
    `gdp_constant_usd` `additive` — the constant-dollar gotcha as metadata.
  - The labels ship in `manifest.json`'s `additivity` map (248 columns across 25
    relations), with `analytics`' in `EXTRA_ADDITIVITY` because dbt cannot see
    Polars output. They are stated rather than derived from the mart, because a
    derived label fails *open* when a mart column is renamed.
  - A `meta:` block can sit below a comment or a `description:`, so a line-wise
    insert that only skips comments writes a second `meta:` key — which PyYAML
    silently resolves to the last, and `check-yaml` does not flag.
- **`fct_emissions_energy` is versioned** because nothing in the repo refs it and
  the release ships it: v2 renamed `co2_per_gdp` to `co2_kg_per_gdp_ppp_2011`. v2
  is aliased back to the bare relation name. v1, a view over v2 that put the old
  column back last with its contract declared in the same order, was removed at
  the end of its window (2026-11-01), so v2 is the only version.
  - **dbt never drops a removed version's relation.** A warehouse file kept
    across builds still holds `marts.fct_emissions_energy_v1` as a view, and
    `publish/export_warehouse.py` exports whole schemas, so a local export ships
    it; every workflow builds from an empty file, so no release does. Drop it with
    `just sql write`.
  - **A `deprecation_date` is enforced.** dbt's own behaviour when
    it passes is a warning and exit 0, so `flags.warn_error_options` promotes
    `DeprecatedModel` and `DeprecatedReference` to errors, failing `dbt parse`.
    `UpcomingReferenceDeprecation` stays a warning — it fires during the
    migration window, which is what the window is for — and so does everything
    else: `error: all` would fail the release on a warning some later dbt adds.
  - **Versioning changes the Dagster asset key, silently.** The default
    translator keys a versioned model on its alias alone, dropping the `marts/`
    prefix and with it the model's materialisation history.
    `FolderGroupDbtTranslator.get_asset_key` puts the schema back.
  - **Select a prefix as `key:"marts/*"`.** A bare `marts/*` reads `marts/` as an
    asset key, finds none, takes everything downstream of the empty set,
    materialises nothing and exits 0. `just materialize-preview` shows what a
    selection resolves to first.
- **The bus matrix is derived from the manifest** (`just bus-matrix`, rendered
  into `docs/WAREHOUSE.md`) — business processes down, conformed dimensions
  across, the one thing groups and contracts do not say.
  - A uniqueness test with a `where` is not a grain: read as one,
    `dim_grid_emission_factors` becomes a dimension every country fact
    "conforms" to.
  - Conformance is exact column-name matching, deliberately; an alias list would
    have hidden the `quote_currency`/`currency_code` split it found in the FX
    models.
  - `tests/test_bus_matrix.py` regenerates and compares, and holds the orphan set
    to `KNOWN_UNCONFORMED` both ways.
