"""Open a published warehouse with the oldest DuckDB its release notes promise.

    python check_reader_floor.py <warehouse.duckdb> <relation> [<relation> ...]

The runner that wrote the file opens any format it wrote, so only an old client
can test the promise. This script is run *by* that old client, in an
environment with nothing but `duckdb` installed, so it imports nothing from this
repo and uses no syntax newer than Python 3.12 (DuckDB 0.10.3 has no 3.13 wheel).

It attaches the file read-only as `warehouse` (the alias the release notes give,
and the catalog name the views were compiled against), counts each relation, and
exits non-zero naming the first one that fails or is empty.
"""

import sys

import duckdb


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    path, relations = argv[1], argv[2:]
    con = duckdb.connect()
    con.execute(f"attach '{path}' as warehouse (read_only)")
    for relation in relations:
        try:
            rows = con.execute(f"select count(*) from warehouse.{relation}").fetchall()[0][0]
        except duckdb.Error as err:
            print(f"duckdb {duckdb.__version__}: {relation} FAILED: {err}", file=sys.stderr)
            return 1
        if not rows:
            print(f"duckdb {duckdb.__version__}: {relation} is empty", file=sys.stderr)
            return 1
        print(f"duckdb {duckdb.__version__}: {relation} {rows}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
