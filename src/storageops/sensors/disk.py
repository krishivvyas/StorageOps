"""Volume-level storage information (free/used/total space)."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DiskUsage:
    path: str
    volume: str  # drive anchor, e.g. "C:\\" on Windows or "/" on POSIX
    total_bytes: int
    used_bytes: int
    free_bytes: int

    @property
    def free_ratio(self) -> float:
        return self.free_bytes / self.total_bytes if self.total_bytes else 0.0


def disk_usage(path: str | os.PathLike[str]) -> DiskUsage:
    """Report usage of the volume that contains ``path``."""
    resolved = Path(path).resolve()
    usage = shutil.disk_usage(resolved)
    return DiskUsage(
        path=str(resolved),
        volume=resolved.anchor,
        total_bytes=usage.total,
        used_bytes=usage.used,
        free_bytes=usage.free,
    )
