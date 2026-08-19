"""Package a built warehouse as a publishable artifact.

Produces a directory holding a checkpointed copy of the database, one zstd
Parquet per modelled table, a manifest of row counts and checksums, `SHA256SUMS`,
an attribution file and release notes. Nothing here touches the network: it reads
a warehouse that already exists.

**The DuckDB copy keeps the source file's name, and that is load-bearing.** dbt
creates views against a catalog named after the file's stem, and their stored SQL
says `warehouse.raw.owid_co2`. Copy the database to `snapshot.duckdb` and every
view raises `Catalog "warehouse" does not exist` while the tables keep working —
a half-broken artifact. The same trap catches consumers, which is why the release
notes have to tell them which alias to `ATTACH` as.

Which schemas ship, who the data belongs to, and what the release notes say are
all the project's to answer; see `scripts/export_warehouse.py`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import duckdb


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def loaded_at(con: duckdb.DuckDBPyConnection) -> str | None:
    """When the pipeline last landed data, which is not when this ran: an export
    of a stale warehouse should look stale."""
    try:
        row = con.execute("select max(inserted_at) from raw._dlt_loads").fetchone()
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
    (rows,) = con.execute(f"select count(*) from {qualified}").fetchone()
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
        bounds = con.execute(
            f"select min({period_column}), max({period_column}) from {qualified}"
        ).fetchone()
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
    period_column: str = "year",
) -> dict:
    """Build `out_dir` from `duckdb_path`. Returns the manifest.

    `extra_manifest` is read from the *copy*, and lets a project add fields the
    generic manifest can't know about. `release_notes` receives the finished
    manifest, the repo slug and the tag.

    `prepare_copy` runs against the copy **writable**, before anything is read or
    measured, and whatever it returns is merged into the manifest. It is the hook
    for a policy that has to hold over the whole artifact rather than over one
    table — rewriting a classified column, say. Doing it here and not in the
    models is what keeps a view and the table it reads in agreement: the copy's
    views recompute from whatever `prepare_copy` left behind, and the checksums,
    the row counts and the Parquet all describe the result rather than the
    input.

    `period_column` is the column whose min/max becomes each table's coverage
    bounds; a table without it is described without them. It's threaded through
    rather than left to `export_table`'s default because a project whose period
    is `month` or `fiscal_year` would otherwise get a manifest silently missing
    coverage for every table.
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
            # Reclaim what the rewrite left behind, so the published file is the
            # size of its contents rather than of its history.
            writable.execute("checkpoint")
        finally:
            writable.close()

    # Read the tables out of the snapshot, not the original: it's the copy that
    # gets published, so the manifest should describe what shipped.
    con = duckdb.connect(str(warehouse_copy), read_only=True)
    try:
        manifest = {
            "tag": tag,
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "data_loaded_at": loaded_at(con),
            **prepared,
            **(extra_manifest(con) if extra_manifest else {}),
            "git_sha": git_sha(),
            "duckdb_version": duckdb.__version__,
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
    sums = [f"{manifest['warehouse']['sha256']}  {manifest['warehouse']['file']}"]
    sums += [f"{t['sha256']}  {t['file']}" for t in manifest["tables"]]
    (dest_dir / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    return manifest
