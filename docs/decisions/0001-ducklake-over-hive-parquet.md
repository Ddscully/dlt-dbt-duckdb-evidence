# 0001. The landing zone is a DuckLake catalog, not a hive-partitioned Parquet archive

Status: accepted 2026-08-28 (#26)

## Context

dlt used to land `raw` inside `data/warehouse.duckdb`, and a hand-written step
(`lake/archive.py`, the `parquet_archive` asset, `just lake`) copied the
warehouse out to a hive-partitioned Parquet archive at `data/lake/`, checked by
a `lake_matches_warehouse` asset check. The archive was optional: a project that
did not want cross-run diffability could drop the layer.

## Decision

dlt lands `raw` in a DuckLake catalog under `data/lakehouse/`, and dbt reads it
through an `attach`. The archive, its asset, its check and its recipe are gone,
and `just clean` removes a leftover `data/lake/`.

## Rejected

- **Keeping the archive beside DuckLake.** It was a second copy of data DuckLake
  already stores as Parquet, with a catalog on top. Keeping both meant keeping
  two answers to *what moved upstream?*.
- **Diffing the files to see what changed.** The archive's output was
  byte-identical run to run, so a diff of the files meant something. DuckLake
  content-addresses its files, so it no longer does; `lake.lakehouse.revisions()`
  compares two snapshots instead. (The change feed does not work either, because
  dlt regenerates its provenance columns on every merged row: `the-lakehouse`.)
- **The archive as the worked example of small partitions.** Its 275 partitions
  averaged ~47 kB, far too small for a real lake. DuckLake prunes on file
  statistics, so the example has nothing left to teach.

## Consequences

- The landing zone is no longer optional. dbt attaches it and every staging model
  reads through it, so dropping it is a decision about where dlt lands, not a
  layer left out (`docs/REUSING_THIS_STACK.md` §7).
- It is the only copy of every landing table, and `just clean` never takes it.
