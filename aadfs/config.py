"""Where the app keeps its files.

Everything the app writes -- saved boards, the cached nflverse downloads, the
results database -- lives under one root so that a container can mount a single
volume and keep it all. `AADFS_DATA` moves that root; without it, paths stay
relative to the working directory exactly as before.

This matters more than it looks. The saved boards are not a cache: a source can
only be graded on the ranking it published at the time, so a board lost to a
redeploy is a week of accuracy data that cannot be reconstructed.
"""

from __future__ import annotations

import os
from pathlib import Path

DATA_ENV = "AADFS_DATA"
DB_ENV = "AADFS_DB"


def _env(name: str) -> str | None:
    """An environment variable, treating blank as unset.

    Compose and shell wrappers routinely export a variable as the empty string
    when it has no value (``${AADFS_DATA:-}``). Taking that literally would
    resolve the data root to the current directory and quietly scatter files
    outside the mounted volume.
    """
    value = (os.environ.get(name) or "").strip()
    return value or None


def data_root() -> Path:
    """The directory holding everything the app writes."""
    return Path(_env(DATA_ENV) or "data")


def data_path(*parts: str) -> Path:
    """A path inside the data root."""
    return data_root().joinpath(*parts)


def cache_dir() -> Path:
    return data_path("cache")


def rankings_dir() -> Path:
    return data_path("rankings")


def salaries_dir() -> Path:
    return data_path("salaries")


def projections_dir() -> Path:
    return data_path("projections")


def database_path() -> Path:
    """The SQLite file. Kept beside the other data unless overridden."""
    explicit = _env(DB_ENV)
    if explicit:
        return Path(explicit)
    if _env(DATA_ENV):
        return data_path("aadfs.db")
    # Unchanged default for anyone already running from a checkout.
    return Path("aadfs.db")


def ensure_dirs() -> None:
    """Create the directories the app writes into."""
    for directory in (cache_dir(), rankings_dir(), salaries_dir(), projections_dir()):
        directory.mkdir(parents=True, exist_ok=True)
