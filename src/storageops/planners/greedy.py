"""Greedy baseline: commit to the best-looking choices until the target is met.

Two ranking keys:

``ratio`` (default): most bytes freed per unit of cost
    The classic greedy for the multiple-choice knapsack problem. Each file's
    allowed actions form a lower convex hull of (benefit, cost) points, e.g.
    KEEP -> COMPRESS -> DELETE. Every hull edge is an "upgrade" with an
    incremental cost per byte. Edges from all files are taken in increasing
    order of that slope, so a file can move from COMPRESS to DELETE when that
    is the next cheapest byte. This matches the proposal's "low-risk,
    high-yield first" rule exactly, and it is the integer rounding of the LP
    relaxation used by branch-and-bound.

``heuristic``: highest h(f, a) first
    (file, action) pairs ranked by the heuristic score. A file may later be
    upgraded to an action that frees more. This variant isolates the effect of
    the heuristic weights in the Phase 4 experiments.

Both skip choices that would delete the last copy of a duplicate group or
exceed the risk budget. Afterwards, redundant actions are removed (costliest
first) while the target stays met. O(m log m) for m candidate pairs, with no
optimality guarantee.
"""

from __future__ import annotations

from typing import Literal

import numpy as np

from storageops.core.state import DELETE, KEEP, CleanupProblem
from storageops.planners.base import Outcome, Planner, SearchStats
from storageops.planners.relaxation import file_hulls

GreedyKey = Literal["ratio", "heuristic"]


class GreedyPlanner(Planner):
    def __init__(self, key: GreedyKey = "ratio", *, prune_redundant: bool = True) -> None:
        if key not in ("ratio", "heuristic"):
            raise ValueError(f"unknown greedy key {key!r}")
        self.key = key
        self.prune_redundant = prune_redundant
        self.name = "greedy" if key == "ratio" else "greedy_heuristic"

    def _solve(self, problem: CleanupProblem, stats: SearchStats) -> Outcome:
        state = _Builder(problem)
        if problem.required_bytes > 0:
            if self.key == "ratio":
                self._by_ratio(problem, state, stats)
            else:
                self._by_heuristic(problem, state, stats)
            if self.prune_redundant and state.done:
                stats.nodes_pruned = state.drop_redundant()
            stats.incumbent_updates = int(state.done)
        return Outcome(state.assignment)

    @staticmethod
    def _by_ratio(problem: CleanupProblem, state: _Builder, stats: SearchStats) -> None:
        hulls = file_hulls(problem.benefit, problem.cost)
        edges = [  # (slope, -delta benefit, file, vertex)
            ((c1 - c0) / (b1 - b0), -(b1 - b0), i, v)
            for i, hull in enumerate(hulls)
            for v, ((b0, c0, _), (b1, c1, _)) in enumerate(zip(hull, hull[1:], strict=False), 1)
        ]
        edges.sort()
        level = [0] * problem.n  # hull vertex each file currently sits on
        for _, _, i, v in edges:
            if state.done:
                break
            stats.nodes_generated += 1
            if level[i] != v - 1:  # an earlier upgrade of this file was rejected
                continue
            if state.try_set(i, hulls[i][v][2]):
                level[i] = v
                stats.nodes_expanded += 1

    @staticmethod
    def _by_heuristic(problem: CleanupProblem, state: _Builder, stats: SearchStats) -> None:
        files, actions = np.nonzero(problem.allowed & (problem.benefit > 0))
        mask = actions != KEEP
        files, actions = files[mask], actions[mask]
        benefit = problem.benefit[files, actions]
        ranking = np.lexsort((-benefit, -problem.heuristic[files, actions]))
        for p in ranking:
            if state.done:
                break
            stats.nodes_generated += 1
            i, j = int(files[p]), int(actions[p])
            current = state.assignment[i]
            if current != KEEP and problem.benefit[i, j] <= problem.benefit[i, current]:
                continue  # only upgrades that free more are useful
            if state.try_set(i, j):
                stats.nodes_expanded += 1


class _Builder:
    """A plan under construction that maintains freed bytes, cost and constraint counters."""

    def __init__(self, problem: CleanupProblem) -> None:
        self.p = problem
        self.assignment = np.full(problem.n, KEEP, dtype=np.int64)
        self.freed = 0
        self.cost = 0.0
        self.deletes = [0] * len(problem.group_sizes)

    @property
    def done(self) -> bool:
        return self.freed >= self.p.required_bytes

    def try_set(self, i: int, j: int) -> bool:
        """Switch file ``i`` to action ``j`` if every constraint still holds."""
        p, old = self.p, int(self.assignment[i])
        new_cost = self.cost - p.cost[i, old] + p.cost[i, j]
        if new_cost > p.risk_budget:
            return False
        group = int(p.group_index[i])
        if group >= 0 and (j == DELETE) != (old == DELETE):
            if j == DELETE:
                if self.deletes[group] + 1 >= p.group_sizes[group]:
                    return False
                self.deletes[group] += 1
            else:
                self.deletes[group] -= 1
        self.freed += int(p.benefit[i, j] - p.benefit[i, old])
        self.cost = float(new_cost)
        self.assignment[i] = j
        return True

    def drop_redundant(self) -> int:
        """Reset to KEEP every action the target does not need, costliest first."""
        chosen = np.flatnonzero(self.assignment != KEEP)
        actions = self.assignment[chosen]
        benefit = self.p.benefit[chosen, actions]
        cost = self.p.cost[chosen, actions]
        dropped = 0
        for q in np.lexsort((benefit, -cost)):
            if self.freed - int(benefit[q]) >= self.p.required_bytes:
                self.try_set(int(chosen[q]), KEEP)  # going back to KEEP is always allowed
                dropped += 1
        return dropped
