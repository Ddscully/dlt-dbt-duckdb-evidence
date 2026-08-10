# The published data release

### 👉 [Latest snapshot](https://github.com/Ddscully/dlt-dbt-duckdb-evidence/releases/latest)

The dashboard is one consumer of the warehouse. The warehouse itself is published
too, so you can use the joined data without running any of this. Each release
carries the whole DuckDB file plus a Parquet per modelled table, `manifest.json`
(row counts, year coverage, SHA-256 per asset) and `SHA256SUMS`.

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
publishers update annually) or on demand, and `scripts/export_warehouse.py`
packages it. `just export-data` does the same thing locally, into `data/export/`.
Tags are dated, `data-YYYY-MM-DD`; `releases/latest/download/…` always resolves
to the newest one, so the URLs above never go stale.

`raw` and `history` ship inside the DuckDB file but not as Parquet. The flat
files are the modelled layers only; anyone who wants dlt's landing tables or the
snapshots downloads the database.

## Four things to know if you're copying this setup

**Alias it `warehouse`.** dbt writes the `staging` views with fully-qualified
SQL, and DuckDB names a catalog after its file, so those views only resolve under
that name. `ATTACH … AS wh` reads the `marts` and `analytics` *tables* fine and
makes every view raise `Catalog "warehouse" does not exist`. Same reason the
export copies the file as `warehouse.duckdb` and not `snapshot-2026-07-30.duckdb`.

**The copy is made with `COPY FROM DATABASE`, not `cp`.** It's consistent
whatever state the source was left in (a crashed run leaves a `.wal` beside it)
and compacted, which is most of why 32 MB of warehouse ships as 29 MB.

**The DuckDB file has a storage format.** It was written by whatever version the
workflow resolved, recorded in `manifest.json`, and older clients may refuse it.
The Parquet files carry no such constraint, which is why both ship.

**`history` is inherited, not rebuilt.** Everything else in the file is built
from scratch each time. The two SCD2 snapshots — OWID's CO₂ estimates and the
grid emission factors — can't be, since a revision only leaves a trace if you
were holding the previous number. Each release downloads its predecessor and
copies `history` in before it builds (`scripts/restore_history.py`), so the
releases accumulate a genuine revision log. `manifest.json` reports how much of
one.

## Licensing

Releases redistribute upstream data, which the repository itself doesn't. All the
sources permit it with attribution, so every release ships an `ATTRIBUTION.md`
naming the publisher and licence per source, and the release notes repeat it.
`ATTRIBUTION` in `scripts/export_warehouse.py` is the single source of truth for
both. Keep it in step with the README's licence section when a source is added.
