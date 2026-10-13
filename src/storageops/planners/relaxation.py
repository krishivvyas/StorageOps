"""LP relaxation of the cleanup problem: an admissible lower bound for branch-and-bound.

Relax "exactly one action per file" to "any convex combination of a file's
actions". For one file, the cheapest way to free x bytes is then its *lower
convex hull* through (0, 0) and its allowed (benefit, cost) points. The hull is
a chain of segments with increasing cost-per-byte slopes.

Because every hull is convex, the relaxed optimum over many files is to take
hull segments from all files in increasing slope order, the last one
fractionally, until the needed bytes are covered (the classic LP solution of
the multiple-choice knapsack problem).

The relaxation drops the duplicate-copy constraint and the risk budget. Removing
constraints can only lower the optimum, so the bound stays admissible: it never
overestimates the true remaining cost.
"""

from __future__ import annotations

import math

import numpy as np


def lower_hull(points: list[tuple[int, float, int]]) -> list[tuple[int, float, int]]:
    """Vertices of the lower convex hull of ``points`` plus the origin, origin first.

    ``points`` are (benefit > 0, cost >= 0, label) triples; the label (an action
    index) is carried through, and the origin's label is -1. The returned chain
    has strictly increasing benefit and non-decreasing slopes.
    """
    cheapest: dict[int, tuple[float, int]] = {}
    for b, c, label in points:
        if b > 0 and c < cheapest.get(b, (math.inf, -1))[0]:
            cheapest[b] = (c, label)
    hull: list[tuple[int, float, int]] = [(0, 0.0, -1)]
    for b, (c, label) in sorted(cheapest.items()):
        while len(hull) >= 2 and _cross(hull[-2], hull[-1], (b, c)) <= 0:
            hull.pop()
        hull.append((b, c, label))
    return hull


def file_hulls(benefit: np.ndarray, cost: np.ndarray) -> list[list[tuple[int, float, int]]]:
    """Lower hull of every file's allowed actions with positive benefit."""
    hulls = []
    for i in range(benefit.shape[0]):
        allowed = np.flatnonzero(np.isfinite(cost[i]) & (benefit[i] > 0))
        hulls.append(lower_hull([(int(benefit[i, j]), float(cost[i, j]), int(j)) for j in allowed]))
    return hulls


def _cross(o: tuple, a: tuple, b: tuple) -> float:  # 2-D cross product of (benefit, cost)
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


class Relaxation:
    """Precomputed hull segments of every file, sorted by slope (cost per byte)."""

    def __init__(self, benefit: np.ndarray, cost: np.ndarray) -> None:
        n = benefit.shape[0]
        seg_file: list[int] = []
        seg_b: list[float] = []
        seg_c: list[float] = []
        self.first_slope = np.full(n, math.inf)  # best cost per byte of each file
        for i, hull in enumerate(file_hulls(benefit, cost)):
            for (b0, c0, _), (b1, c1, _) in zip(hull, hull[1:], strict=False):
                seg_file.append(i)
                seg_b.append(b1 - b0)
                seg_c.append(c1 - c0)
            if len(hull) > 1:
                self.first_slope[i] = hull[1][1] / hull[1][0]

        files = np.asarray(seg_file, dtype=np.int64)
        db = np.asarray(seg_b, dtype=float)
        dc = np.asarray(seg_c, dtype=float)
        order = np.argsort(dc / db, kind="stable") if len(db) else np.empty(0, dtype=np.int64)
        self._file, self._b, self._c = files[order], db[order], dc[order]

    @property
    def segments(self) -> int:
        return len(self._b)

    def bound(self, first_file: int, need: float) -> float:
        """Minimum relaxed cost of freeing ``need`` bytes using files ``first_file..n-1``.

        Returns +inf when even the relaxation cannot free that much.
        """
        if need <= 0:
            return 0.0
        mask = self._file >= first_file
        b = self._b[mask]
        if b.size == 0:
            return math.inf
        covered = np.cumsum(b)
        if covered[-1] < need:
            return math.inf
        idx = int(np.searchsorted(covered, need))  # first segment that completes the need
        c = self._c[mask]
        before = covered[idx - 1] if idx else 0.0
        return float(c[:idx].sum() + c[idx] * (need - before) / b[idx])
