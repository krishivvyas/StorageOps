"""Where StorageOps writes its own files (activity logs and operation journals).

Everything goes under one folder: ``~/.storageops`` by default, or the folder
named by the ``STORAGEOPS_HOME`` environment variable. Tests point it at a
temporary directory.
"""

from __future__ import annotations

import os
from pathlib import Path


def storageops_home() -> Path:
    override = os.environ.get("STORAGEOPS_HOME")
    return Path(override) if override else Path.home() / ".storageops"


def journal_dir() -> Path:
    """Per-run JSON Lines journals of file operations (and dry runs)."""
    return storageops_home() / "journal"


def log_dir() -> Path:
    """Rotating activity log of everything StorageOps does."""
    return storageops_home() / "logs"
