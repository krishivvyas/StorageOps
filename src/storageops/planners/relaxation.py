"""LP relaxation of the cleanup problem: an admissible lower bound for branch-and-bound.

Relax "exactly one action per file" to "any convex combination of a file's
actions". For one file, the cheapest way to free x bytes is then its *lower
convex hull* through (0, 0) and its allowed (benefit, cost) points. The hull is
a chain of segments with increasing cost-per-byte slopes.

Because every hull is convex, the relaxed optimum over many independent units
is to take hull segments from all units in increasing slope order, the last one
fractionally, until the needed bytes are covered (the classic LP solution of
the multiple-choice knapsack problem).

**Duplicate groups.** If the relaxation ignored "never delete every copy", it
would cheaply "delete" all copies of a duplicate, which have low risk, and the
bound would collapse on duplicate-heavy instances (measured in Phase 4: gaps of
45-65% instead of 0%). So when all copies of a group are candidates and sit
next to each other in the decision order, the group becomes one unit. Its
feasible set is the union over "copy m is kept" of the members' combined
options. The convex hull of that union is the lower hull of the union of the
Minkowski sums (each member's full hull, except member m without DELETE). This
is the exact convexification, so it is still admissible.

The risk budget is still relaxed. Dropping a constraint can only lower the
optimum, so the bound never overestimates the true remaining cost.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from storageops.core.state import DELETE

Vertex = tuple[int, float, int]  # (benefit, cost, label)


def lower_hull(points: list[Vertex]) -> list[Vertex]:
    """Vertices of the lower convex hull of ``points`` plus the origin, origin first.

    ``points`` are (benefit > 0, cost >= 0, label) triples; the label (an action
    index) is carried through, and the origin's label is -1. The returned chain
    has strictly increasing benefit and non-decreasing slopes.
    """
    cheapest: dict[int, tuple[float, int]] = {}
    for b, c, label in points:
        if b > 0 and c < cheapest.get(b, (math.inf, -1))[0]:
            cheapest[b] = (c, label)
    hull: list[Vertex] = [(0, 0.0, -1)]
    for b, (c, label) in sorted(cheapest.items()):
        while len(hull) >= 2 and _cross(hull[-2], hull[-1], (b, c)) <= 0:
            hull.pop()
        hull.append((b, c, label))
    return hull


def file_hull(benefit_row: np.ndarray, cost_row: np.ndarray) -> list[Vertex]:
    allowed = np.flatnonzero(np.isfinite(cost_row) & (benefit_row > 0))
    return lower_hull([(int(benefit_row[j]), float(cost_row[j]), int(j)) for j in allowed])


def file_hulls(benefit: np.ndarray, cost: np.ndarray) -> list[list[Vertex]]:
    """Lower hull of every file's allowed actions with positive benefit."""
    return [file_hull(benefit[i], cost[i]) for i in range(benefit.shape[0])]


def first_slopes(hulls: Sequence[list[Vertex]]) -> np.ndarray:
    """Cheapest cost per byte of each file (+inf when it can free nothing)."""
    return np.array([h[1][1] / h[1][0] if len(h) > 1 else math.inf for h in hulls], dtype=float)


def full_groups(group_index: np.ndarray, group_sizes: Sequence[int]) -> dict[int, np.ndarray]:
    """Groups whose every copy is a candidate, so the constraint actually binds.

    If some copy is not a candidate, it is always kept, and the constraint can
    never be violated.
    """
    groups: dict[int, list[int]] = {}
    for i, g in enumerate(group_index.tolist()):
        if g >= 0:
            groups.setdefault(g, []).append(i)
    return {
        g: np.array(members)
        for g, members in groups.items()
        if len(members) >= 2 and len(members) == group_sizes[g]
    }


def group_chain(hulls: Sequence[list[Vertex]], no_delete: Sequence[list[Vertex]]) -> list[Vertex]:
    """Convexified option chain of a group that may not delete every member."""
    points: list[Vertex] = []
    for kept in range(len(hulls)):
        chains = [no_delete[m] if m == kept else hulls[m] for m in range(len(hulls))]
        points.extend(_minkowski(chains)[1:])
    return lower_hull(points)


def _minkowski(chains: Sequence[list[Vertex]]) -> list[Vertex]:
    """Minkowski sum of convex chains from the origin: merge their edges by slope."""
    edges = sorted(
        ((c1 - c0) / (b1 - b0), b1 - b0, c1 - c0)
        for chain in chains
        for (b0, c0, _), (b1, c1, _) in zip(chain, chain[1:], strict=False)
    )
    vertices: list[Vertex] = [(0, 0.0, -1)]
    for _, db, dc in edges:
        b, c, _ = vertices[-1]
        vertices.append((b + db, c + dc, 0))
    return vertices


def _cross(o: tuple, a: tuple, b: tuple) -> float:  # 2-D cross product of (benefit, cost)
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


class Relaxation:
    """Hull segments of every unit (a file, or a contiguous duplicate group), sorted by slope.

    ``bound(k, need)`` relaxes the subproblem over files ``k..n-1``. A group
    unit counts only if it lies wholly inside that range. A group that ``k``
    cuts through contributes its remaining members' individual hulls instead,
    which is weaker but still admissible.
    """

    def __init__(
        self,
        benefit: np.ndarray,
        cost: np.ndarray,
        group_index: np.ndarray | None = None,
        group_sizes: Sequence[int] = (),
    ) -> None:
        n = benefit.shape[0]
        hulls = file_hulls(benefit, cost)
        self.first_slope = first_slopes(hulls)
        self._unit_start = np.arange(n)  # first file of the unit containing each file
        self._unit_end = np.arange(1, n + 1)
        units: list[tuple[int, list[Vertex]]] = [(i, hulls[i]) for i in range(n)]
        self._member_segments: dict[int, np.ndarray] = {}  # per-file segments inside groups

        if group_index is not None:
            for members in full_groups(group_index, group_sizes).values():
                lo, hi = int(members.min()), int(members.max()) + 1
                if hi - lo != len(members):
                    continue  # not contiguous in this order: keep the members separate
                no_delete = []
                for i in members:
                    capped = cost[i].copy()
                    capped[DELETE] = math.inf
                    no_delete.append(file_hull(benefit[i], capped))
                    self._member_segments[int(i)] = _segments(hulls[i])
                chain = group_chain([hulls[i] for i in members], no_delete)
                for i in members:
                    units[i] = (lo, [])
                units[lo] = (lo, chain)
                self._unit_start[lo:hi] = lo
                self._unit_end[lo:hi] = hi

        starts, db, dc = [], [], []
        for start, chain in units:
            seg = _segments(chain)
            starts.extend([start] * len(seg))
            db.extend(seg[:, 0].tolist())
            dc.extend(seg[:, 1].tolist())
        self._start = np.asarray(starts, dtype=np.int64)
        self._b = np.asarray(db, dtype=float)
        self._c = np.asarray(dc, dtype=float)
        order = np.argsort(self._c / self._b, kind="stable") if len(db) else np.empty(0, int)
        self._start, self._b, self._c = self._start[order], self._b[order], self._c[order]
        self._slope = self._c / self._b if len(db) else np.empty(0)

    @property
    def segments(self) -> int:
        return len(self._b)

    def bound(self, first_file: int, need: float) -> float:
        """Minimum relaxed cost of freeing ``need`` bytes using files ``first_file..n-1``.

        Returns +inf when even the relaxation cannot free that much.
        """
        if need <= 0:
            return 0.0
        mask = self._start >= first_file
        b, c = self._b[mask], self._c[mask]
        n = len(self._unit_start)
        if first_file < n and self._unit_start[first_file] < first_file:
            # first_file is inside a group whose earlier members are already decided
            extra = [
                self._member_segments[i] for i in range(first_file, int(self._unit_end[first_file]))
            ]
            extra = np.concatenate(extra) if extra else np.empty((0, 2))
            if len(extra):
                extra = extra[np.argsort(extra[:, 1] / extra[:, 0], kind="stable")]
                pos = np.searchsorted(self._slope[mask], extra[:, 1] / extra[:, 0])
                b = np.insert(b, pos, extra[:, 0])
                c = np.insert(c, pos, extra[:, 1])
        if b.size == 0:
            return math.inf
        covered = np.cumsum(b)
        if covered[-1] < need:
            return math.inf
        idx = int(np.searchsorted(covered, need))  # first segment that completes the need
        before = covered[idx - 1] if idx else 0.0
        return float(c[:idx].sum() + c[idx] * (need - before) / b[idx])


def _segments(chain: list[Vertex]) -> np.ndarray:
    """(delta benefit, delta cost) of each edge of a chain, shape (edges, 2)."""
    if len(chain) < 2:
        return np.empty((0, 2))
    return np.array(
        [(b1 - b0, c1 - c0) for (b0, c0, _), (b1, c1, _) in zip(chain, chain[1:], strict=False)],
        dtype=float,
    )
