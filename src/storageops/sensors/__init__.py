"""Phase 1 - Sensors: read-only perception of the storage environment."""

from storageops.sensors.dataset import (
    category_summary,
    duplicate_summary,
    largest_files,
    load_dataset,
    records_to_frame,
    save_dataset,
)
from storageops.sensors.disk import DiskUsage, disk_usage
from storageops.sensors.environment import EnvironmentSnapshot, capture_environment
from storageops.sensors.hashing import DuplicateReport, find_duplicates
from storageops.sensors.scanner import FileScanner, ScanResult, ScanStats

__all__ = [
    "DiskUsage",
    "DuplicateReport",
    "EnvironmentSnapshot",
    "FileScanner",
    "ScanResult",
    "ScanStats",
    "capture_environment",
    "category_summary",
    "disk_usage",
    "duplicate_summary",
    "find_duplicates",
    "largest_files",
    "load_dataset",
    "records_to_frame",
    "save_dataset",
]
