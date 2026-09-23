# The published data release

### 👉 [Latest snapshot](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/releases/latest)

The dashboard is one consumer of the warehouse. The warehouse itself is published
too, so you can use the joined data without running any of this. Each release
carries:

- `warehouse.duckdb`, the whole file dbt builds — `staging`, `marts`,
  `analytics` and `history`, but never `raw`, which lives in the landing zone
- a Parquet per modelled table
- `lakehouse.tar.gz`, the one landing table no rebuild can refetch within
  Open-Meteo's daily budget (`raw.om_weather_daily`), as a relocatable DuckLake
  catalog
- `manifest.json` (row counts, year coverage, SHA-256 per asset), `SHA256SUMS`
  and `ATTRIBUTION.md`

**`manifest.json` also says which columns may be summed.** Its `additivity` map
labels every numeric column of every published `marts` and `analytics` table
`additive`, `semi_additive`, `non_additive` or `not_a_measure` — half of them
are non-additive, and a Parquet file has no way
of telling you that `sum(renewables_share_pct)` is nonsense that returns a
number. Where a column is `semi_additive` its description says which direction
fails: `population` adds across countries and gives person-years across years,
`cumulative_co2` is a stock that recounts every earlier year.

**And it says what each column means.** Its `relations` map gives every
published table's description and each column's type and description, read back
from the comments dbt's `persist_docs` writes into the file — so the same text
is on the tables in `warehouse.duckdb` (`select comment from duckdb_columns()`).
A `null` description means none has been written yet, and the release notes say
how many have: not every mart column is documented. The Parquet files carry none
of it; they have nowhere to put it.

## Querying it

DuckDB reads a remote database over HTTPS, so you can query it where it sits:

```sql
INSTALL httpfs; LOAD httpfs;
ATTACH 'https://github.com/Ddscully/dlt-dbt-duckdb-evidence/releases/latest/download/warehouse.duckdb'
       AS warehouse (READ_ONLY);
SELECT country_name, year, co2_mt, renewables_share_pct
FROM warehouse.marts.fct_emissions_energy
WHERE year = 2024;
```

Or take a single table as a flat file, no DuckDB required:

```sql
SELECT * FROM read_parquet('https://github.com/Ddscully/dlt-dbt-duckdb-evidence/releases/latest/download/marts__fct_emissions_energy.parquet');
```

`.github/workflows/release-data.yml` builds it from the live sources monthly (the
publishers update annually) or on demand, and `publish/export_warehouse.py`
packages it. `just export-data` does the same thing locally, into `data/export/`.
Tags are dated, `data-YYYY-MM-DD`; `releases/latest/download/…` always resolves
to the newest one, so the URLs above never go stale.

`history` ships inside the DuckDB file but not as Parquet: the flat files are the
modelled layers only, so anyone who wants the snapshots downloads the database.
The rest of `raw` is never published, since it holds dlt's merge scratch with
customer ids in the clear; rebuilding it is `just run`.

## What to know before building on it

**Alias it `warehouse`.** dbt writes the `intermediate` views with
fully-qualified SQL, and DuckDB names a catalog after its file, so those views
only resolve under that name. `ATTACH … AS wh` reads every *table* fine and
makes the views raise `Catalog "warehouse" does not exist`. Same reason the
export copies the file as `warehouse.duckdb` and not `snapshot-2026-07-30.duckdb`.

**The copy is made with `COPY FROM DATABASE`, not `cp`.** It's consistent
whatever state the source was left in (a crashed run leaves a `.wal` beside it)
and compacted, which is most of why 32 MB of warehouse ships as 29 MB. The copy
is made **twice**: once to snapshot the source, and again after the
pseudonymisation below has rewritten a column across a million rows. `CHECKPOINT`
would not do — DuckDB reuses freed blocks but never returns them to the
filesystem, so checkpointing after the rewrite leaves the file 13% *larger*.

**`customer_id` is pseudonymised, and it is the only column that is.** It ships
as a salted digest of the publisher's own id — applied to every copy of the
column in the file, the `staging` tables and dbt's stored test failures
included — so it joins the retail
tables to each other and to the previous release, and does not join them back to
the source workbook. The salt is stable across releases and is not in this
repository.

The columns *beside* it are not masked and the release is not anonymous: 98.6% of
the 5,881 customers are unique on `(first_order_gbp, net_revenue_gbp, n_orders)`
with no identifier at all. Treat the retail tables as personal data, because that
is what they are. [`DATA_PROTECTION.md`](./DATA_PROTECTION.md) has the
classification, the measurement and the decisions.

**The DuckDB file has a storage format, and it is not the version that wrote
it.** `manifest.json` records both, because they answer different questions:
`duckdb_version` is the writer, `storage_version` is what a reader has to
support. Every DuckDB from 1.x writes format **64** by default, which every
DuckDB from `v0.10.0` on reads, so a file written by 1.5.5 opens on a client
from early 2024, and the release notes say so rather than hedging.

The export refuses to publish a format above that ceiling, and
`tests/test_export.py` fails if the installed DuckDB stops writing it. That
matters because DuckDB 2.0 ships a new default storage format, and
`pyproject.toml` asks only for `duckdb>=1.1`, with no upper bound, so the change would otherwise arrive as one line of a grouped
monthly Dependabot PR, pass every test in the repo — they all write and read with
the same binary — and first show up as a consumer unable to open a release.
Raising the ceiling is a decision that strands old readers, not a lockfile edit.
The Parquet files carry no such constraint, which is why both ship.

**`history` is inherited, not rebuilt.** Everything else in the file is built
from scratch each time. The two SCD2 snapshots — OWID's CO₂ estimates and the
grid emission factors — can't be, since a revision only leaves a trace if you
were holding the previous number. Each release downloads its predecessor and
copies `history` in before it builds (`publish/restore_history.py`), so the
releases accumulate a genuine revision log. `manifest.json` reports how much of
one.

## Licensing

Releases redistribute upstream data, which the repository itself doesn't. All the
sources permit it with attribution, so every release ships an `ATTRIBUTION.md`
naming the publisher and licence per source, and the release notes repeat it.
`ATTRIBUTION` in `publish/export_warehouse.py` is the single source of truth for
both, and `tests/test_export.py` holds it to the README's licence section.
