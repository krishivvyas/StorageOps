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

PLANNER_NAMES = ("bnb", "greedy", "greedy-heuristic")


def make_planner(name: str, *, time_limit_s: float | None = 10.0) -> Planner:
    """Build a planner by its user-facing name (CLI ``--planner`` / GUI choice)."""
    if name == "bnb":
        return BranchAndBoundPlanner(time_limit_s=time_limit_s)
    if name == "greedy":
        return GreedyPlanner("ratio")
    if name == "greedy-heuristic":
        return GreedyPlanner("heuristic")
    raise ValueError(f"unknown planner {name!r}; choose from {', '.join(PLANNER_NAMES)}")


__all__ = [
    "PLANNERS",
    "PLANNER_NAMES",
    "make_planner",
    "BranchAndBoundPlanner",
    "ExhaustivePlanner",
    "GreedyPlanner",
    "Planner",
    "PlannerResult",
    "SearchStats",
    "plan_to_frame",
]
