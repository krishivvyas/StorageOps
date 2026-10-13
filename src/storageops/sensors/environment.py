"""Combine all sensors into one environment snapshot: the planner's initial percept."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from storageops.config import Config
from storageops.models import ScanIssue
from storageops.sensors.dataset import load_dataset, records_to_frame, save_dataset
from storageops.sensors.disk import DiskUsage, disk_usage
from storageops.sensors.hashing import DuplicateReport, find_duplicates
from storageops.sensors.scanner import FileScanner, ProgressCallback, ScanStats

SCHEMA_VERSION = 1
log = logging.getLogger(__name__)


@dataclass
class EnvironmentSnapshot:
    """Everything the agent perceives about a storage environment at one moment."""

    root: str
    captured_at: float  # epoch seconds
    disk: DiskUsage
    files: pd.DataFrame
    scan_stats: ScanStats
    duplicates: DuplicateReport | None  # None when duplicate detection was disabled
    issues: list[ScanIssue]

    @property
    def total_bytes(self) -> int:
        return int(self.files["size_bytes"].sum())

    def save(self, path: str | Path) -> tuple[Path, Path]:
        """Write the file table to ``path`` (.csv/.parquet) plus a ``<stem>.meta.json`` sidecar."""
        data_path = save_dataset(self.files, path)
        meta_path = _meta_path(data_path)
        duplicates = None
        if self.duplicates is not None:
            duplicates = asdict(self.duplicates)
            duplicates.pop("issues")  # already included in the top-level issue list
        meta = {
            "schema_version": SCHEMA_VERSION,
            "root": self.root,
            "captured_at": self.captured_at,
            "disk": asdict(self.disk),
            "scan_stats": asdict(self.scan_stats),
            "duplicates": duplicates,
            "issues": [asdict(i) for i in self.issues],
        }
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return data_path, meta_path

    @classmethod
    def load(cls, path: str | Path) -> EnvironmentSnapshot:
        data_path = Path(path)
        meta = json.loads(_meta_path(data_path).read_text(encoding="utf-8"))
        if meta.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"Unsupported snapshot schema: {meta.get('schema_version')!r}")
        return cls(
            root=meta["root"],
            captured_at=meta["captured_at"],
            disk=DiskUsage(**meta["disk"]),
            files=load_dataset(data_path),
            scan_stats=ScanStats(**meta["scan_stats"]),
            duplicates=DuplicateReport(**meta["duplicates"]) if meta["duplicates"] else None,
            issues=[ScanIssue(**i) for i in meta["issues"]],
        )


def capture_environment(
    root: str | os.PathLike[str],
    config: Config | None = None,
    *,
    detect_duplicates: bool = True,
    progress: ProgressCallback | None = None,
    now: float | None = None,
) -> EnvironmentSnapshot:
    """Scan ``root``, detect duplicates and read volume usage. Read-only."""
    config = config or Config()
    log.info("scan started (read-only): %s", root)
    result = FileScanner(config.scanner, progress=progress, now=now).scan(root)
    log.info("scan finished: %d files, %d unreadable paths, %.2fs", len(result.records),
             len(result.issues), result.stats.duration_s)  # fmt: skip

    records = result.records
    issues = list(result.issues)
    duplicates = None
    if detect_duplicates:
        records, duplicates = find_duplicates(records, config.hashing)
        issues.extend(duplicates.issues)

    return EnvironmentSnapshot(
        root=result.root,
        captured_at=result.started_at,
        disk=disk_usage(result.root),
        files=records_to_frame(records),
        scan_stats=result.stats,
        duplicates=duplicates,
        issues=issues,
    )


def _meta_path(data_path: Path) -> Path:
    return data_path.with_name(f"{data_path.stem}.meta.json")
