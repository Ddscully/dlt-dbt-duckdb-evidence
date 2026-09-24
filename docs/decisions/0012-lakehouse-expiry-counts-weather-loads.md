# 0012. Lakehouse expiry keeps the last two weather loads, not a window of days

Status: accepted 2026-09-24

## Context

DuckLake keeps every snapshot until one is expired, and a file stays on disk
while any snapshot reads it. Every `replace` load rewrites its whole table, so
the landing zone only grew: on 2026-09-24 the catalog held 234 snapshots over
22 days and 332 MB of Parquet on disk: 300 MB the catalog recorded, of which
57 MB was live, and 8 orphaned files (32 MB) that one interrupted load left
and the catalog never recorded.

Expiry deletes history as well as bytes. The one history anything reads is the
weather table's: `weather_revisions_are_derivable` diffs its last two loads,
because ERA5 restates ERA5T two to three months later. The released catalog
carries no lineage at all — `publish()` builds it with `create table … as
select` — so the working catalog is the only place that history exists.
`ducklake_expire_snapshots` is catalog-wide: no retention can keep weather's
history without also keeping every other table's dead rewrites alongside it.

Measured on a copy of that catalog, counting back from its newest snapshot:

| Kept | Parquet left | Weather versions left |
|---|---|---|
| everything | 332 MB | 9 |
| 14 days | 321 MB | 8 |
| 7 days | 166 MB | 5 |
| 1 day | 153 MB | 4 |
| last two weather loads | 109 MB (orphans deleted too) | 2 |
| only the newest snapshot | 57 MB (orphans deleted too) | 1 — nothing to diff |

Expiring surfaced a defect in `table_versions()`: it returned the snapshot a
live file *began* at, which can itself have expired, and `revisions()` then
failed with `No snapshot found at version 212`. It now maps each change to the
earliest snapshot still held at or after it.

## Decision

`lake.lakehouse.expire()` expires every snapshot older than the
`KEEP_WEATHER_LOADS`-th newest weather version (2), deletes the files only
those snapshots read, and, for a data path on disk, deletes orphans more than a
day old. It runs as `lake/snapshot_expiry` after the loads in `full_refresh`,
in `just run` after `ingest`, and by hand as `just lakehouse-expire`.

## Rejected

- **A window of days from now** — `older_than => now() - interval 'N days'`,
  or the catalog's own `expire_older_than` option. It is DuckLake's native
  unit and one line. It is wrong for a catalog that sits idle, which is the
  normal state of this one on a laptop: this catalog had been untouched for
  seven days when measured, so any window under a week would have expired
  every snapshot but the newest and left the weather check nothing to diff.
  The diff needs the last two *loads*, whenever they happened.
- **A window anchored on the newest snapshot.** Idle-proof, but it still
  counts time rather than loads: a weather load older than the window loses
  its pair, and a busy day of development keeps dozens of snapshots nobody
  diffs.
- **`ducklake_merge_adjacent_files`.** It merged 2 weather files into 1 and
  freed nothing measurable. The delete files that dlt's merge writes stop
  adjacent files from merging.
- **Orphan deletion in a bucket.** `ducklake_delete_orphaned_files` deletes
  whatever under the data path the catalog does not record, and a bucket
  prefix can hold what this catalog does not own: a bucket-root data path
  would contain every fixture run's `test-pipeline/` prefix. On disk,
  `data/lakehouse/data/` belongs to the catalog alone.

## Consequences

- A restatement older than the previous weather load can no longer be diffed
  in the working copy. "What did ERA5 revise this quarter" needs a larger
  `KEEP_WEATHER_LOADS`, and pays for it in every other table's rewrites.
- `just lakehouse-expire N` with a larger N holds only until the next
  `full_refresh`, which expires back to the constant.
- Revisit if a second table's history gets a reader, or if DuckLake grows
  per-table expiry.
