"""Staged duplicate-file detection.

Hashing every file is expensive, so candidates are narrowed in three stages:

1. Group by exact size: files of different size cannot be identical.
2. Partial hash (head + tail bytes) within each size group.
3. Full content hash, only for files whose partial hashes still collide.

Hard links to the same file are not treated as duplicates: removing one frees no space.
"""

from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace

from storageops.config import HashingConfig
from storageops.models import FileRecord, ScanIssue


@dataclass
class DuplicateReport:
    groups: int = 0
    duplicate_files: int = 0  # redundant copies, excluding each group's retained original
    reclaimable_bytes: int = 0
    files_hashed: int = 0
    bytes_read: int = 0
    issues: list[ScanIssue] = field(default_factory=list)


def find_duplicates(
    records: Sequence[FileRecord], config: HashingConfig | None = None
) -> tuple[list[FileRecord], DuplicateReport]:
    """Detect identical files and annotate them.

    Returns a new list in the same order as ``records``. Duplicates get
    ``content_hash`` and ``duplicate_group`` set. Within each group the oldest
    file (then the shallowest, then the shortest path) is kept as the original;
    every other member has ``is_duplicate=True``. This is only a default label;
    the planner decides which copy, if any, to act on.
    """
    config = config or HashingConfig()
    report = DuplicateReport()

    by_size: dict[int, list[int]] = defaultdict(list)
    for i, record in enumerate(records):
        if record.size_bytes >= config.min_size_bytes:
            by_size[record.size_bytes].append(i)

    confirmed: list[tuple[str, list[int]]] = []
    for size, indices in by_size.items():
        if len(indices) < 2:
            continue
        indices = _drop_hard_links(records, indices, report)
        report.files_hashed += len(indices)
        partial_groups = _split_by_digest(
            records, indices, lambda r: _partial_digest(r.path, r.size_bytes, config), report
        )
        if size <= 2 * config.partial_bytes:
            confirmed.extend(partial_groups)  # partial hash already covered the whole file
            continue
        for _, group in partial_groups:
            confirmed.extend(
                _split_by_digest(records, group, lambda r: _full_digest(r.path, config), report)
            )

    # Number groups by reclaimable space, largest first.
    confirmed.sort(key=lambda g: records[g[1][0]].size_bytes * (len(g[1]) - 1), reverse=True)

    updated = list(records)
    for group_id, (digest, indices) in enumerate(confirmed, start=1):
        original = min(
            indices,
            key=lambda i: (
                records[i].modified_ts,
                records[i].depth,
                len(records[i].path),
                records[i].path,
            ),
        )
        for i in indices:
            updated[i] = replace(
                records[i],
                content_hash=digest,
                duplicate_group=group_id,
                is_duplicate=i != original,
            )
        report.groups += 1
        report.duplicate_files += len(indices) - 1
        report.reclaimable_bytes += records[indices[0]].size_bytes * (len(indices) - 1)

    return updated, report


def _drop_hard_links(
    records: Sequence[FileRecord], indices: list[int], report: DuplicateReport
) -> list[int]:
    """Keep one path per physical file. Uses a full stat, because scandir's cached
    stat leaves the inode and device fields at zero on Windows."""
    seen: set[tuple[int, int]] = set()
    kept: list[int] = []
    for i in indices:
        try:
            st = os.stat(records[i].path)
        except OSError as exc:
            report.issues.append(ScanIssue(records[i].path, f"{type(exc).__name__}: {exc}"))
            continue
        if st.st_nlink > 1 and st.st_ino:
            identity = (st.st_dev, st.st_ino)
            if identity in seen:
                continue
            seen.add(identity)
        kept.append(i)
    return kept


def _split_by_digest(
    records: Sequence[FileRecord],
    indices: list[int],
    digest_fn: Callable[[FileRecord], tuple[str, int]],
    report: DuplicateReport,
) -> list[tuple[str, list[int]]]:
    """Bucket ``indices`` by digest and return only the buckets with two or more members."""
    buckets: dict[str, list[int]] = defaultdict(list)
    for i in indices:
        try:
            digest, bytes_read = digest_fn(records[i])
        except OSError as exc:
            report.issues.append(ScanIssue(records[i].path, f"{type(exc).__name__}: {exc}"))
            continue
        report.bytes_read += bytes_read
        buckets[digest].append(i)
    return [(digest, group) for digest, group in buckets.items() if len(group) > 1]


def _partial_digest(path: str, size: int, config: HashingConfig) -> tuple[str, int]:
    if size <= 2 * config.partial_bytes:
        return _full_digest(path, config)
    hasher = hashlib.new(config.algorithm)
    with open(path, "rb") as fh:
        head = fh.read(config.partial_bytes)
        fh.seek(size - config.partial_bytes)
        tail = fh.read(config.partial_bytes)
    hasher.update(head)
    hasher.update(tail)
    return hasher.hexdigest(), len(head) + len(tail)


def _full_digest(path: str, config: HashingConfig) -> tuple[str, int]:
    hasher = hashlib.new(config.algorithm)
    bytes_read = 0
    with open(path, "rb") as fh:
        while chunk := fh.read(config.chunk_size_bytes):
            hasher.update(chunk)
            bytes_read += len(chunk)
    return hasher.hexdigest(), bytes_read
