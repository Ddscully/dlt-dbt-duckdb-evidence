"""Writing a file whole or not at all — `modern_data_stack.files`.

Each test is one of the ways a cached download or extract went wrong: a partial
file under the real name, an owner-only file another user cannot read, and
leftovers that pile up.
"""

from __future__ import annotations

import os
import time

import pytest

from modern_data_stack import files


def _umask() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return mask


def test_the_file_appears_only_when_the_block_finishes(tmp_path):
    dest = tmp_path / "archive.zip"
    with files.replaced_when_whole(dest) as out:
        out.write(b"whole")
        assert not dest.exists()
    assert dest.read_bytes() == b"whole"
    assert list(tmp_path.iterdir()) == [dest]


def test_the_file_gets_the_permissions_any_new_file_would(tmp_path):
    """`tempfile.mkstemp` makes its file owner-only, and a rename keeps the
    mode, so the cache would hold a file the run container, or anyone else
    sharing the volume, cannot open."""
    dest = tmp_path / "archive.zip"
    with files.replaced_when_whole(dest) as out:
        out.write(b"whole")
    assert dest.stat().st_mode & 0o777 == 0o666 & ~_umask()


def test_a_failed_write_leaves_the_destination_as_it_was_and_nothing_else(tmp_path):
    dest = tmp_path / "archive.zip"
    dest.write_bytes(b"the previous copy")
    with pytest.raises(OSError, match="No space"), files.replaced_when_whole(dest) as out:
        out.write(b"half")
        raise OSError("No space left on device")
    assert dest.read_bytes() == b"the previous copy"
    assert list(tmp_path.iterdir()) == [dest]


def test_a_failed_check_leaves_nothing_behind(tmp_path):
    def refuse(path):
        raise ValueError("not a zip")

    dest = tmp_path / "archive.zip"
    with (
        pytest.raises(ValueError, match="not a zip"),
        files.replaced_when_whole(dest, check=refuse) as out,
    ):
        out.write(b"<html>")
    assert list(tmp_path.iterdir()) == []


def test_two_writes_at_once_never_share_a_temporary_file(tmp_path):
    dest = tmp_path / "archive.zip"
    with files.replaced_when_whole(dest) as first, files.replaced_when_whole(dest) as second:
        assert first.name != second.name
        first.write(b"one")
        second.write(b"two")
    assert dest.read_bytes() in (b"one", b"two")


def test_a_write_sweeps_the_leftovers_of_a_killed_one_and_spares_a_live_one(tmp_path):
    """A process killed outright runs no cleanup, so its temporary file stays;
    a unique name means the next write never overwrites it. Only one older than
    any write in progress could be is removed."""
    dest = tmp_path / "archive.zip"
    killed = tmp_path / "archive.zip.0123abcd.part"
    in_progress = tmp_path / "archive.zip.4567ef01.part"
    unrelated = tmp_path / "other.zip.89ab.part"
    for part in (killed, in_progress, unrelated):
        part.write_bytes(b"partial")
    two_days_ago = time.time() - 2 * files.STALE_PART_SECONDS
    os.utime(killed, (two_days_ago, two_days_ago))
    os.utime(unrelated, (two_days_ago, two_days_ago))

    with files.replaced_when_whole(dest) as out:
        out.write(b"whole")

    assert not killed.exists()
    assert in_progress.exists()
    assert unrelated.exists()
