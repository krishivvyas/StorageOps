"""Common planner interface, search statistics and results."""

from __future__ import annotations

import math
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np
import pandas as pd

from storageops.core.actions import ACTIONS
from storageops.core.state import CleanupProblem
from storageops.models import Action, CleanupPlan


@dataclass
class SearchStats:
    """Search effort, reported the same way by every planner.

    For tree search, ``nodes_generated`` counts children created,
    ``nodes_expanded`` counts nodes whose children were generated, and
    ``nodes_pruned`` counts children discarded by bounds or constraints. The
    greedy planner maps these to: candidate pairs examined, actions selected, and
    redundant actions removed.
    """

    nodes_generated: int = 0
    nodes_expanded: int = 0
    nodes_pruned: int = 0
    incumbent_updates: int = 0  # times a better complete plan was found
    elapsed_s: float = 0.0
    limit_reached: bool = False  # stopped by a time or node limit


@dataclass(frozen=True)
class Outcome:
    """What a planner's ``_solve`` returns: an action index per file in problem order."""

    assignment: np.ndarray
    proven_optimal: bool = False
    lower_bound: float = math.nan  # best proven lower bound on the optimal cost
    proven_infeasible: bool = False


@dataclass(frozen=True)
class PlannerResult:
    planner: str
    plan: CleanupPlan
    assignment: tuple[Action, ...]  # one action per problem file, in problem order
    stats: SearchStats
    proven_optimal: bool
    lower_bound: float
    proven_infeasible: bool = False

    @property
    def status(self) -> str:
        if self.proven_infeasible:
            return "infeasible"
        if not self.plan.is_goal:
            return "no plan found"
        return "optimal" if self.proven_optimal else "feasible"

    @property
    def gap(self) -> float:
        """Relative optimality gap (cost - lower bound) / cost; 0 when proven optimal."""
        if not self.plan.is_goal or math.isnan(self.lower_bound):
            return math.nan
        if self.plan.total_cost <= 0:
            return 0.0
        return max(0.0, (self.plan.total_cost - self.lower_bound) / self.plan.total_cost)


class Planner(ABC):
    """A strategy that maps a :class:`CleanupProblem` to a cleanup plan."""

    name: str = "planner"

    def plan(self, problem: CleanupProblem) -> PlannerResult:
        stats = SearchStats()
        start = time.perf_counter()
        outcome = self._solve(problem, stats)
        stats.elapsed_s = time.perf_counter() - start
        assignment = tuple(ACTIONS[int(j)] for j in outcome.assignment)
        return PlannerResult(
            planner=self.name,
            plan=problem.to_plan(assignment),
            assignment=assignment,
            stats=stats,
            proven_optimal=outcome.proven_optimal,
            lower_bound=outcome.lower_bound,
            proven_infeasible=outcome.proven_infeasible,
        )

    @abstractmethod
    def _solve(self, problem: CleanupProblem, stats: SearchStats) -> Outcome: ...


def plan_to_frame(plan: CleanupPlan) -> pd.DataFrame:
    """One row per proposed action, the format the review GUI and exports use."""
    columns = ["path", "action", "size_bytes", "benefit_bytes", "cost", "risk"]
    rows = [
        (a.path, a.action.value, a.size_bytes, a.benefit_bytes, a.cost, a.risk)
        for a in plan.actions
    ]
    return pd.DataFrame(rows, columns=columns)
