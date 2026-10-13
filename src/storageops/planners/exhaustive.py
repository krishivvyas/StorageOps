"""Exhaustive search: the ground truth for small instances.

Plain depth-first enumeration using only the public state-space interface
(CleanupProblem.initial_state / successors / is_goal), with no bounding. Its
single shortcut is the goal cut-off: once a state meets the target, every
extension costs at least as much, because costs are >= 0. Use it to verify
branch-and-bound and to measure the speed-up bounding gives.
"""

from __future__ import annotations

import math

import numpy as np

from storageops.core.actions import ACTION_INDEX
from storageops.core.state import KEEP, CleanupProblem
from storageops.planners.base import Outcome, Planner, SearchStats


class ExhaustivePlanner(Planner):
    name = "exhaustive"

    def __init__(self, max_files: int = 16) -> None:
        self.max_files = max_files

    def _solve(self, problem: CleanupProblem, stats: SearchStats) -> Outcome:
        if problem.n > self.max_files:
            raise ValueError(
                f"exhaustive search is limited to {self.max_files} files (got {problem.n})"
            )
        best_cost, best_state = math.inf, None
        stack = [problem.initial_state()]
        while stack:
            state = stack.pop()
            if problem.is_goal(state):
                if state.cost < best_cost:
                    best_cost, best_state = state.cost, state
                    stats.incumbent_updates += 1
                continue
            stats.nodes_expanded += 1
            for _, child in problem.successors(state):
                stats.nodes_generated += 1
                stack.append(child)

        assignment = np.full(problem.n, KEEP, dtype=np.int64)
        if best_state is not None:
            assignment[: best_state.depth] = [ACTION_INDEX[a] for a in best_state.actions]
        return Outcome(
            assignment,
            proven_optimal=best_state is not None,
            lower_bound=best_cost,
            proven_infeasible=best_state is None,
        )
