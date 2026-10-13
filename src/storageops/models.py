"""Domain models shared by every StorageOps layer.

These types contain no I/O, so the sensors, planners, benchmark generator and
GUI all exchange the same representation of a file.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class Action(StrEnum):
    """Cleanup actions the planner may assign to a file.

    KEEP      Leave the file untouched.
    DELETE    Send the file to the Recycle Bin (recoverable).
    ARCHIVE   Compress into an archive, verify the archive, then remove the original.
    COMPRESS  Transparent in-place compression; the file stays usable at its path.
    MOVE      Relocate the file without compression.

    Every action other than KEEP needs explicit user approval before it runs.
    """

    KEEP = "KEEP"
    DELETE = "DELETE"
    ARCHIVE = "ARCHIVE"
    COMPRESS = "COMPRESS"
    MOVE = "MOVE"

    @property
    def removes_original(self) -> bool:
        """True when the file is no longer present at its original path afterwards."""
        return self in (Action.DELETE, Action.ARCHIVE, Action.MOVE)


class FileCategory(StrEnum):
    """Coarse file type used by the risk model and the GUI."""

    DOCUMENT = "document"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    COMPRESSED = "compressed"
    INSTALLER = "installer"
    EXECUTABLE = "executable"
    SOURCE_CODE = "source_code"
    DATA = "data"
    TEMPORARY = "temporary"
    CACHE = "cache"
    LOG = "log"
    SYSTEM = "system"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class FileRecord:
    """Metadata for one file: a single row of the agent's percept.

    Timestamps are POSIX epoch seconds. ``idle_days`` relies on last-access
    times, which Windows may not update; treat it as a weaker signal than
    ``age_days``.
    """

    path: str
    name: str
    extension: str  # lower-case with leading dot, "" if none
    category: FileCategory
    size_bytes: int
    created_ts: float
    modified_ts: float
    accessed_ts: float
    age_days: float  # days since last modification
    idle_days: float  # days since last access or modification, whichever is later
    depth: int  # directory depth below the scan root (0 = directly in root)
    is_hidden: bool = False
    is_system: bool = False
    is_readonly: bool = False
    content_hash: str | None = None
    duplicate_group: int | None = None
    is_duplicate: bool = False  # True for redundant copies, False for the retained original

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["category"] = self.category.value
        return data


@dataclass(frozen=True, slots=True)
class ScanIssue:
    """A non-fatal problem met while sensing, e.g. a permission-denied directory."""

    path: str
    message: str
