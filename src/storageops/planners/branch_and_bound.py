"""Branch-and-bound: minimum-cost cleanup plan, proven optimal when the search finishes.

Depth-first search over the state space of core.state (one decision per file),
with backtracking on mutable arrays so that a node costs O(1) memory, not O(n).

* **Incumbent.** Starts from the better of the two greedy plans: an upper bound U on
  the optimal cost.
* **Goal cut-off.** Once a node frees the required bytes, keeping every remaining
  file is its cheapest completion (costs are >= 0). The node is recorded and
  not expanded further.
* **Bounding.** A child with path cost g is pruned when g + LP(remaining) >= U.
  The LP relaxation (planners.relaxation) never overestimates, so no optimal
  plan is ever pruned.
* **Constraints.** Children that delete the last copy of a duplicate group, or
  exceed the risk budget, are not generated.
* **Branching order.** Files: most cost-efficient first (``order="ratio"``), or by
  size, or as given. Actions at each node, chosen by ``action_order``:

  - ``"bound"`` (default): smallest optimistic total cost g + LP first (best-first
    value ordering, which usually finds the optimum early);
  - ``"heuristic"``: highest h(f, a) first (the heuristic guides the search).
* **Limits.** With a time or node limit the best plan found so far is returned,
  together with the root LP bound, so the optimality gap can be reported.
"""

from __future__ import annotations

import math
import time
from typing import Literal

import numpy as np
import pandas as pd

from storageops.core.actions import ACTION_INDEX
from storageops.core.state import DELETE, KEEP, CleanupProblem
from storageops.planners.base import Outcome, Planner, SearchStats
from storageops.planners.greedy import GreedyPlanner
from storageops.planners.relaxation import Relaxation, file_hulls, first_slopes, full_groups

FileOrder = Literal["ratio", "benefit", "problem"]
ActionOrder = Literal["bound", "heuristic"]
_EPS = 1e-9
_CLOCK_EVERY = 256  # iterations between time-limit checks


class BranchAndBoundPlanner(Planner):
    name = "branch_and_bound"

    def __init__(
        self,
        *,
        time_limit_s: float | None = 10.0,
        node_limit: int | None = None,
        order: FileOrder = "ratio",
        action_order: ActionOrder = "bound",
        greedy_incumbent: bool = True,
    ) -> None:
        if order not in ("ratio", "benefit", "problem"):
            raise ValueError(f"unknown file order {order!r}")
        if action_order not in ("bound", "heuristic"):
            raise ValueError(f"unknown action order {action_order!r}")
        self.action_order = action_order
        self.time_limit_s = time_limit_s
        self.node_limit = node_limit
        self.order = order
        self.greedy_incumbent = greedy_incumbent

    def _solve(self, problem: CleanupProblem, stats: SearchStats) -> Outcome:
        n, required = problem.n, problem.required_bytes
        if required == 0:
            return Outcome(np.full(n, KEEP, dtype=np.int64), proven_optimal=True, lower_bound=0.0)

        perm = self._file_order(problem)
        benefit = problem.benefit[perm]
        cost = problem.cost[perm]
        group_of = problem.group_index[perm]
        group_sizes = problem.group_sizes
        relaxation = Relaxation(benefit, cost, group_of, group_sizes)
        root_bound = relaxation.bound(0, required)
        if math.isinf(root_bound) or root_bound > problem.risk_budget:
            return Outcome(
                np.full(n, KEEP, dtype=np.int64),
                proven_optimal=True,
                lower_bound=math.inf,
                proven_infeasible=True,
            )
        heuristic = problem.heuristic[perm]
        allowed = [np.flatnonzero(np.isfinite(cost[i])).tolist() for i in range(n)]
        by_heuristic = [
            sorted(js, key=lambda j, i=i: -heuristic[i, j]) for i, js in enumerate(allowed)
        ]

        best_cost = math.inf
        best = np.full(n, KEEP, dtype=np.int64)
        if self.greedy_incumbent:
            for key in ("ratio", "heuristic"):
                greedy = GreedyPlanner(key).plan(problem)
                if greedy.plan.is_goal and greedy.plan.total_cost < best_cost:
                    best_cost = greedy.plan.total_cost
                    best = np.array([ACTION_INDEX[a] for a in greedy.assignment])[perm]
                    stats.incumbent_updates += 1

        assignment = np.full(n, KEEP, dtype=np.int64)
        deletes = [0] * len(group_sizes)
        freed, path_cost = 0, 0.0
        budget = problem.risk_budget
        deadline = None if self.time_limit_s is None else time.perf_counter() + self.time_limit_s

        def children(k: int) -> list[int]:
            if self.action_order == "heuristic":
                return by_heuristic[k]
            need = required - freed
            return sorted(
                allowed[k],
                key=lambda j: cost[k, j] + relaxation.bound(k + 1, need - benefit[k, j]),
            )

        # Each frame: [file k, next option position, action currently applied or -1, options]
        stack: list[list] = [[0, 0, -1, children(0)]]
        stats.nodes_expanded = 1
        iterations = 0
        while stack:
            iterations += 1
            if iterations % _CLOCK_EVERY == 0 and self._out_of_budget(deadline, stats):
                stats.limit_reached = True
                break

            frame = stack[-1]
            k, pos, applied, options = frame
            if applied >= 0:  # undo the child explored last
                freed -= int(benefit[k, applied])
                path_cost -= float(cost[k, applied])
                if applied == DELETE and group_of[k] >= 0:
                    deletes[group_of[k]] -= 1
                assignment[k] = KEEP
                frame[2] = -1
            if pos >= len(options):
                stack.pop()
                continue
            j = options[pos]
            frame[1] = pos + 1
            stats.nodes_generated += 1

            child_cost = path_cost + float(cost[k, j])
            group = int(group_of[k])
            if (
                child_cost > budget
                or child_cost >= best_cost - _EPS
                or (j == DELETE and group >= 0 and deletes[group] + 1 >= group_sizes[group])
            ):
                stats.nodes_pruned += 1
                continue

            # apply child
            freed += int(benefit[k, j])
            path_cost = child_cost
            assignment[k] = j
            if j == DELETE and group >= 0:
                deletes[group] += 1
            frame[2] = j

            if freed >= required:  # goal: keeping everything else is the cheapest completion
                best_cost, best = path_cost, assignment.copy()
                stats.incumbent_updates += 1
                continue
            if k + 1 == n:
                continue
            if path_cost + relaxation.bound(k + 1, required - freed) >= best_cost - _EPS:
                stats.nodes_pruned += 1
                continue
            stats.nodes_expanded += 1
            stack.append([k + 1, 0, -1, children(k + 1)])

        result = np.full(n, KEEP, dtype=np.int64)
        result[perm] = best
        exhausted = not stats.limit_reached
        return Outcome(
            result,
            proven_optimal=exhausted and math.isfinite(best_cost),
            lower_bound=best_cost if exhausted else root_bound,
            proven_infeasible=exhausted and math.isinf(best_cost),
        )

    def _file_order(self, problem: CleanupProblem) -> np.ndarray:
        """Decision order. Members of a binding duplicate group are kept next to each
        other so that the relaxation can bound the group as one unit."""
        max_benefit = np.where(problem.allowed, problem.benefit, 0).max(axis=1)
        slope = first_slopes(file_hulls(problem.benefit, problem.cost))
        unit = np.arange(problem.n)
        for members in full_groups(problem.group_index, problem.group_sizes).values():
            unit[members] = members.min()
        if self.order == "problem":
            key = (unit.astype(float),)
        elif self.order == "benefit":
            key = (-_per_unit(max_benefit, unit, "max"),)
        else:  # cheapest bytes first, then larger files
            key = (-_per_unit(max_benefit, unit, "max"), _per_unit(slope, unit, "min"))
        # last key is the primary sort key; unit and position keep groups together and stable
        return np.lexsort((np.arange(problem.n), unit, *key))

    def _out_of_budget(self, deadline: float | None, stats: SearchStats) -> bool:
        if deadline is not None and time.perf_counter() > deadline:
            return True
        return self.node_limit is not None and stats.nodes_generated >= self.node_limit


def _per_unit(values: np.ndarray, unit: np.ndarray, how: str) -> np.ndarray:
    """Replace each file's value by the ``how`` ("min"/"max") over its unit (group)."""
    return pd.Series(values, dtype=float).groupby(unit).transform(how).to_numpy()
