"""Always-on activity log: a human-readable record of everything StorageOps did.

The CLI and the GUI call :func:`setup_logging` at start-up. Library code only
uses ``logging.getLogger(__name__)``, so importing StorageOps elsewhere never
writes files on its own.

Log file: ``<storageops home>/logs/storageops.log``, rotated at 2 MB with 10
old files kept. Every line is timestamped and states what happened, where, and
whether it was a dry run. File operations are also written, in full detail, to
the per-run journal (``executor/journal.py``).
"""

from __future__ import annotations

import getpass
import logging
import os
import platform
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from storageops import __version__
from storageops.paths import log_dir

LOGGER_NAME = "storageops"
LOG_FILE_NAME = "storageops.log"
_FORMAT = "%(asctime)s  %(levelname)-7s  %(name)-28s  %(message)s"


def log_file() -> Path:
    return log_dir() / LOG_FILE_NAME


def setup_logging(level: int = logging.INFO) -> Path:
    """Attach the rotating file handler (idempotent; follows STORAGEOPS_HOME changes)."""
    path = log_file()
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    for handler in list(logger.handlers):
        if getattr(handler, "_storageops", False):
            if Path(handler.baseFilename) == path:
                return path
            logger.removeHandler(handler)
            handler.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=10, encoding="utf-8")
    handler.setFormatter(logging.Formatter(_FORMAT))
    handler._storageops = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.info(
        "session start: StorageOps %s, Python %s, %s, user %s, pid %d, argv %s",
        __version__, platform.python_version(), platform.platform(), _user(), os.getpid(),
        sys.argv,
    )  # fmt: skip
    return path


def _user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover - platform-specific
        return "unknown"
