"""Phase 3 - Planners: greedy baseline, branch-and-bound, and exhaustive reference."""

from storageops.planners.base import Planner, PlannerResult, SearchStats, plan_to_frame
from storageops.planners.branch_and_bound import BranchAndBoundPlanner
from storageops.planners.exhaustive import ExhaustivePlanner
from storageops.planners.greedy import GreedyPlanner

PLANNERS: dict[str, type[Planner]] = {
    "greedy": GreedyPlanner,
    "bnb": BranchAndBoundPlanner,
    "exhaustive": ExhaustivePlanner,
}

__all__ = [
    "PLANNERS",
    "BranchAndBoundPlanner",
    "ExhaustivePlanner",
    "GreedyPlanner",
    "Planner",
    "PlannerResult",
    "SearchStats",
    "plan_to_frame",
]
