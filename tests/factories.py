"""Builders for synthetic FileRecords: exact control over metadata, no disk access."""

from __future__ import annotations

import ntpath

import pandas as pd

from storageops.models import FileCategory, FileRecord
from storageops.sensors.dataset import records_to_frame
from tests.conftest import DAY, NOW


def make_record(
    path: str,
    size: int = 1000,
    category: FileCategory = FileCategory.OTHER,
    age_days: float = 365.0,
    idle_days: float | None = None,
    *,
    duplicate_group: int | None = None,
    is_duplicate: bool = False,
    hidden: bool = False,
    readonly: bool = False,
    system: bool = False,
) -> FileRecord:
    idle = age_days if idle_days is None else idle_days
    name = ntpath.basename(path)
    return FileRecord(
        path=path,
        name=name,
        extension=ntpath.splitext(name)[1].lower(),
        category=category,
        size_bytes=size,
        created_ts=NOW - age_days * DAY,
        modified_ts=NOW - age_days * DAY,
        accessed_ts=NOW - idle * DAY,
        age_days=age_days,
        idle_days=idle,
        depth=path.count("\\") - 1,
        is_hidden=hidden,
        is_system=system,
        is_readonly=readonly,
        content_hash=f"hash{duplicate_group}" if duplicate_group is not None else None,
        duplicate_group=duplicate_group,
        is_duplicate=is_duplicate,
    )


def make_frame(*records: FileRecord) -> pd.DataFrame:
    return records_to_frame(records)
