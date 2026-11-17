"""Write a file so that no reader, now or later, sees it half-written.

A cache serves whatever sits at a name, forever: a download or an extract that
stops partway and leaves a short file under the real name is read as whole on
every later run. So the bytes go to a temporary file beside the destination and
are renamed onto it only once they are whole. A rename within one directory is
atomic, which is why the temporary file is a sibling and not in `/tmp`.

Three properties, each the answer to a way it went wrong:

* **The temporary name is unique per write**, so two loads filling a cold cache
  at once cannot interleave their bytes in one file.
* **It is created with `open`, not `tempfile.mkstemp`**, so the file gets the
  process's usual permissions. `mkstemp` makes it owner-only, and the rename
  carries that onto the destination, where a second user or container sharing
  the cache can no longer read it.
* **A failed write removes its file, and a write first sweeps any left by a
  process that was killed outright** (where no cleanup runs), so a full disk is
  not kept full and the leftovers do not accumulate.
"""

from __future__ import annotations

import glob
import time
import uuid
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

# A temporary file this old belongs to no running write. A day, because a
# download is bounded by a per-read timeout, not a total one.
STALE_PART_SECONDS = 24 * 3600


@contextmanager
def replaced_when_whole(
    dest: str | Path,
    *,
    check: Callable[[Path], None] | None = None,
    stale_after: float = STALE_PART_SECONDS,
) -> Generator[BinaryIO]:
    """A handle to write `dest` through; `dest` changes only if the block finishes
    and `check`, given the written file, does not raise.

    On any exception, from the block or from `check`, the temporary file is
    removed and the exception propagates; `dest` is left as it was.
    """
    dest = Path(dest)
    sweep_stale_parts(dest, stale_after)
    tmp = dest.with_name(f"{dest.name}.{uuid.uuid4().hex}.part")
    try:
        with tmp.open("xb") as out:
            yield out
        if check is not None:
            check(tmp)
        tmp.replace(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def sweep_stale_parts(dest: str | Path, older_than: float = STALE_PART_SECONDS) -> list[Path]:
    """Remove the temporary files of writes to `dest` older than `older_than`
    seconds, and return them. A younger one may be another write in progress."""
    dest = Path(dest)
    cutoff = time.time() - older_than
    removed = []
    for part in dest.parent.glob(f"{glob.escape(dest.name)}.*.part"):
        try:
            if part.stat().st_mtime < cutoff:
                part.unlink()
                removed.append(part)
        except FileNotFoundError:  # another sweep, or its write finishing
            continue
    return removed
