"""On/off switches read from the environment, spelled one way everywhere.

Every switch reads through `flag`, so the spellings that turn one on are a
single set: three copies of it would let a fourth spelling reach one switch and
not the others, and a switch that reads as off does nothing, with no error.
"""

from __future__ import annotations

import os

TRUE = frozenset({"1", "true", "yes"})


def flag(name: str) -> bool:
    """True when `name` is set to one of `TRUE`, in any case; anything else is off."""
    return os.environ.get(name, "").lower() in TRUE
