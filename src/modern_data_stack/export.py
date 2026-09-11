"""Package a built warehouse as a publishable artifact.

Produces a directory holding a checkpointed copy of the database, one zstd
Parquet per modelled table, a manifest of row counts and checksums, `SHA256SUMS`,
an attribution file and release notes. Nothing here touches the network: it reads
a warehouse that already exists.

**The DuckDB copy keeps the source file's name.** DuckDB names a catalog after
the file's stem and dbt qualifies view SQL with it (`warehouse.marts.…`), so a
copy renamed `snapshot.duckdb` has every view raise `Catalog "warehouse" does
not exist` while the tables work. Consumers hit the same trap, which is why the
release notes say which alias to `ATTACH` as.

**The file's storage format is not the writer's version.** DuckDB 1.x writes
storage version 64 by default, readable by every DuckDB since v0.10.0, so the
manifest records `storage_version` beside `duckdb_version`, and `export()` takes
a ceiling it refuses to exceed — a DuckDB bump that changed the format would
pass every test, since they write and read with one binary.

Which schemas ship, who the data belongs to, and what the release notes say are
all the project's to answer; see `publish/export_warehouse.py`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from .db import qualify, row, scalar


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def storage_version(path: Path) -> int:
    """The storage format version recorded in a DuckDB file's header.

    Read off the bytes because no SQL, pragma or connection property reports it;
    DuckDB exposes it only on the write side (`ATTACH … (STORAGE_VERSION …)`).

    The header is an 8-byte checksum, the 4-byte magic `DUCK`, then the version as
    a little-endian uint64. Measured against files written with an explicit
    `STORAGE_VERSION` on DuckDB 1.5.5:

        64  v0.10.0 … v1.1.3      67  v1.4.x
        65  v1.2.x                68  v1.5.x
        66  v1.3.x

    so a *lower* number is the more widely readable file, and 64 is the floor
    DuckDB still offers.
    """
    with path.open("rb") as fh:
        head = fh.read(20)
    if len(head) < 20 or head[8:12] != b"DUCK":
        raise ValueError(f"not a DuckDB database file: {path}")
    return int.from_bytes(head[12:20], "little")


def git_sha() -> str | None:
    """The commit the export was built from, for reproducibility."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip() or None


def repo_slug() -> str:
    """`owner/name`, for the download URLs in the release notes. Actions sets
    `GITHUB_REPOSITORY`; locally it comes off the origin remote so a fork's
    export links to the fork."""
    env = os.environ.get("GITHUB_REPOSITORY")
    if env:
        return env
    try:
        url = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "OWNER/REPO"
    match = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", url)
    return match.group(1) if match else "OWNER/REPO"


def default_tag(now: datetime | None = None) -> str:
    """Snapshot tags are dated, not semver: the schema is stable and it's the
    data that moves. Re-running on the same day overwrites the same release."""
    return f"data-{(now or datetime.now(UTC)):%Y-%m-%d}"


def published_tables(
    con: duckdb.DuckDBPyConnection, schemas: tuple[str, ...]
) -> list[tuple[str, str]]:
    """Every table/view in the given schemas, dlt bookkeeping excluded."""
    rows = con.execute(
        """
        select table_schema, table_name
        from information_schema.tables
        where table_schema in (select unnest($schemas))
          and table_name not like '\\_%' escape '\\'
        order by table_schema, table_name
        """,
        {"schemas": list(schemas)},
    ).fetchall()
    return [(schema, table) for schema, table in rows]


def loaded_at(
    con: duckdb.DuckDBPyConnection,
    raw_schema: str = "raw",
    raw_database: str | None = None,
) -> str | None:
    """When the pipeline last landed data, which is not when this ran: an export
    of a stale warehouse should look stale.

    Pass `raw_database` when `raw` lives in an attached catalog (and attach it
    to `con`). Unqualified, the read either fails — returning `None` — or, on a
    warehouse that still holds an old in-file `raw`, returns a plausible wrong
    timestamp.
    """
    try:
        row = con.execute(
            f"select max(inserted_at) from {qualify(raw_database, raw_schema, '_dlt_loads')}"
        ).fetchone()
    except duckdb.Error:
        return None
    return row[0].astimezone(UTC).isoformat() if row and row[0] else None


def snapshot_warehouse(duckdb_path: Path, dest: Path) -> None:
    """Write a compacted, WAL-free copy of the warehouse to `dest`.

    `COPY FROM DATABASE` rather than a file copy so the result is consistent
    however the source was left (a crashed run can leave a `.wal` beside it), and
    so it's compacted rather than carrying the free space of a refresh. `dest`'s
    stem has to match the source's — see the module docstring.
    """
    dest.unlink(missing_ok=True)
    con = duckdb.connect(str(dest))
    try:
        con.execute(f"attach '{duckdb_path}' as source_wh (read_only)")
        con.execute(f"copy from database source_wh to {dest.stem}")
        con.execute("detach source_wh")
    finally:
        con.close()


def export_table(
    con: duckdb.DuckDBPyConnection,
    schema: str,
    table: str,
    out_dir: Path,
    period_column: str = "year",
) -> dict:
    """Write one table as Parquet and describe it for the manifest.

    The coverage bounds land under `years` whatever `period_column` is called —
    the manifest's shape is what consumers parse, so it doesn't change with the
    project's period.
    """
    qualified = f'"{schema}"."{table}"'
    path = out_dir / f"{schema}__{table}.parquet"
    con.execute(f"copy (select * from {qualified}) to '{path}' (format parquet, compression zstd)")

    columns = [c[0] for c in con.execute(f"describe {qualified}").fetchall()]
    rows = scalar(con, f"select count(*) from {qualified}")
    entry = {
        "table": f"{schema}.{table}",
        "file": path.name,
        "rows": rows,
        "columns": len(columns),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }
    # Dimensions have no period column, so coverage is reported where it applies.
    if period_column in columns:
        bounds = row(con, f"select min({period_column}), max({period_column}) from {qualified}")
        entry["years"] = list(bounds) if bounds[0] is not None else None
    return entry


def export(
    duckdb_path: str,
    out_dir: str,
    schemas: tuple[str, ...],
    attribution: str,
    release_notes: Callable[[dict, str, str], str],
    tag: str | None = None,
    repo: str | None = None,
    grain: str | None = None,
    extra_manifest: Callable[[duckdb.DuckDBPyConnection], dict] | None = None,
    prepare_copy: Callable[[duckdb.DuckDBPyConnection], dict] | None = None,
    extra_artifacts: Callable[[Path], dict] | None = None,
    read_loaded_at: Callable[[duckdb.DuckDBPyConnection], str | None] | None = None,
    period_column: str = "year",
    max_storage_version: int | None = None,
) -> dict:
    """Build `out_dir` from `duckdb_path`. Returns the manifest.

    `extra_manifest` is read from the *copy*, and lets a project add fields the
    generic manifest can't know about. `release_notes` receives the finished
    manifest, the repo slug and the tag.

    `prepare_copy` runs against the copy, writable, before anything is read or
    measured; what it returns is merged into the manifest. It is the hook for a
    policy over the whole artifact (rewriting a classified column), so the
    checksums, row counts and Parquet describe the result, and the copy's views
    recompute from it.

    `read_loaded_at` replaces the default read of `raw._dlt_loads` from the
    copy, for a project whose `raw` sits in an attached catalog.
    `extra_artifacts` writes further files into `out_dir` and returns manifest
    fields. `period_column`'s min/max becomes each table's coverage bounds.

    `max_storage_version` is the format ceiling (see `storage_version`). No
    default: it is a promise to the project's consumers, meaningful only beside
    the minimum reader version the project states. `None` records the format and
    refuses nothing.
    """
    src = Path(duckdb_path)
    if not src.exists():
        raise FileNotFoundError(f"no warehouse at {src} — build one first")

    tag = tag or default_tag()
    dest_dir = Path(out_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    # The copy keeps the source's file name (the catalog-name trap above), so an
    # `--out data` would delete the warehouse it was asked to package.
    warehouse_copy = dest_dir / src.name
    if warehouse_copy.resolve() == src.resolve():
        raise ValueError(f"--out {dest_dir} would overwrite the source warehouse {src}")
    snapshot_warehouse(src, warehouse_copy)

    prepared: dict = {}
    if prepare_copy is not None:
        writable = duckdb.connect(str(warehouse_copy))
        try:
            prepared = prepare_copy(writable) or {}
        finally:
            writable.close()
        # Copy again rather than `CHECKPOINT`: DuckDB never returns freed blocks
        # to the filesystem, so after rewriting a large column a checkpointed file
        # is larger than before; only `COPY FROM DATABASE` compacts. Into a
        # directory, because a sibling file would need a different stem — and the
        # stem is the catalog name the views were compiled against.
        staging_dir = dest_dir / ".compacting"
        staging_dir.mkdir(exist_ok=True)
        compacted = staging_dir / warehouse_copy.name
        try:
            snapshot_warehouse(warehouse_copy, compacted)
            compacted.replace(warehouse_copy)
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)

    # Measured on the finished copy, the file that is uploaded.
    published_storage = storage_version(warehouse_copy)
    # `>`: publishing at the ceiling is the ordinary case. Raised before the
    # Parquet, manifest and notes are written; the copy is left for inspection.
    if max_storage_version is not None and published_storage > max_storage_version:
        raise ValueError(
            f"refusing to publish {warehouse_copy.name}: storage version "
            f"{published_storage}, above the {max_storage_version} this project "
            f"promises. DuckDB {duckdb.__version__} wrote it, and the writer's "
            "version is not the constraint — the format is, and every client "
            "older than it can no longer open the release. Raising the ceiling "
            "is a decision about which readers to strand, not a lockfile edit."
        )

    # Read from the copy, so the manifest describes what shipped.
    con = duckdb.connect(str(warehouse_copy), read_only=True)
    try:
        manifest = {
            "tag": tag,
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "data_loaded_at": (read_loaded_at or loaded_at)(con),
            **prepared,
            **(extra_artifacts(dest_dir) if extra_artifacts else {}),
            **(extra_manifest(con) if extra_manifest else {}),
            "git_sha": git_sha(),
            "duckdb_version": duckdb.__version__,
            # What decides whether a consumer can open the file.
            "storage_version": published_storage,
            "grain": grain,
            "warehouse": {
                "file": warehouse_copy.name,
                "bytes": warehouse_copy.stat().st_size,
                "sha256": sha256(warehouse_copy),
            },
            "tables": [
                export_table(con, schema, table, dest_dir, period_column)
                for schema, table in published_tables(con, schemas)
            ],
        }
    finally:
        con.close()

    (dest_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (dest_dir / "ATTRIBUTION.md").write_text(attribution)
    (dest_dir / "RELEASE_NOTES.md").write_text(release_notes(manifest, repo or repo_slug(), tag))
    # Walk the directory rather than list what was written, so anything an
    # `extra_artifacts` hook added is covered too. The four excluded files
    # describe the release and cannot checksum themselves.
    described = {"SHA256SUMS", "manifest.json", "RELEASE_NOTES.md", "ATTRIBUTION.md"}
    sums = [
        f"{sha256(path)}  {path.relative_to(dest_dir).as_posix()}"
        for path in sorted(dest_dir.rglob("*"))
        if path.is_file() and path.relative_to(dest_dir).as_posix() not in described
    ]
    (dest_dir / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    return manifest
