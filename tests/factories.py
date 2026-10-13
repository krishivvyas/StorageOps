"""Builders for synthetic FileRecords: exact control over metadata, no disk access."""

from __future__ import annotations

import math
import ntpath

import numpy as np
import pandas as pd

from storageops.core.state import CleanupProblem
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


def make_problem(benefit, cost, groups=None, group_sizes=(), *, free=0, target=0, budget=None,
                 heuristic=None):  # fmt: skip
    """A CleanupProblem from literal matrices (columns: KEEP, DELETE, ARCHIVE, COMPRESS, MOVE)."""
    benefit = np.array(benefit, dtype=np.int64)
    cost = np.array(cost, dtype=float)
    n = len(benefit)
    if heuristic is None:
        heuristic = np.zeros_like(cost)
    heuristic = np.where(np.isfinite(cost), np.asarray(heuristic, dtype=float), -math.inf)
    return CleanupProblem(
        paths=tuple(f"f{i}" for i in range(n)),
        sizes=benefit.max(axis=1) if n else np.zeros(0, dtype=np.int64),
        risk=np.zeros(n),
        benefit=benefit.reshape(n, 5),
        cost=cost.reshape(n, 5),
        heuristic=heuristic.reshape(n, 5),
        group_index=np.array(groups if groups is not None else [-1] * n, dtype=np.int64),
        group_sizes=tuple(group_sizes),
        free_bytes=free,
        target_free_bytes=target,
        risk_budget=math.inf if budget is None else budget,
    )


def random_problem(rng: np.random.Generator, n: int, *, budget: float | None = None,
                   target_fraction: float = 0.4) -> CleanupProblem:  # fmt: skip
    """Random instance shaped like real ones: DELETE frees everything, ARCHIVE/COMPRESS a
    fraction, MOVE sometimes; some actions disallowed; a few duplicate pairs."""
    size = rng.integers(1, 1000, n)
    risk = rng.random(n)
    benefit = np.zeros((n, 5), dtype=np.int64)
    cost = np.full((n, 5), math.inf)
    cost[:, 0] = 0.0
    ratio = rng.uniform(0.2, 1.0, n)
    benefit[:, 1], cost[:, 1] = size, risk + 0.02
    benefit[:, 2], cost[:, 2] = (size * (1 - ratio)).astype(np.int64), 0.4 * risk + 0.04
    benefit[:, 3], cost[:, 3] = (size * (1 - ratio) * 0.6).astype(np.int64), 0.1 * risk + 0.03
    benefit[:, 4], cost[:, 4] = size, 0.25 * risk + 0.03
    disallow = rng.random((n, 5)) < 0.35
    disallow[:, 0] = False
    cost[disallow] = math.inf
    groups = np.full(n, -1)
    pairs = n // 4
    for g in range(pairs):
        groups[2 * g : 2 * g + 2] = g
    target = int(size.sum() * target_fraction)
    return make_problem(benefit, cost, groups, [2] * pairs, target=target, budget=budget,
                        heuristic=rng.random((n, 5)))  # fmt: skip
