"""The boundary between this repo and everyone downstream of it.

Three modules that either produce an artifact someone else consumes or read the
last one back:

* `build_report.py` — the Evidence site.
* `export_warehouse.py` — the monthly data release: the DuckDB copy, the Parquet
  files, the lakehouse tarball, checksums and notes. This is where the personal
  data policy is applied and where the storage-format ceiling is enforced, which
  is the whole reason it is a layer and not a helper.
* `restore_history.py` — the previous release's unreproducible tables, carried
  forward so the snapshot accumulates a real revision log and the weather
  archive keeps deepening.

They were in `scripts/` until 2026-09-01, which made `orchestration/assets.py`
import the top of its own dependency graph out of a directory named for one-off
utilities. What is left in `scripts/` is genuinely one-off: seed transcription,
fixture re-recording, a disclosure measurement.
"""
