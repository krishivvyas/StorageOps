"""Run planners on benchmark instances and collect one tidy row per (instance, planner)."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

import numpy as np
import pandas as pd

from storageops.benchmark.generator import generate_instance
from storageops.benchmark.profiles import InstanceSpec
from storageops.config import Config
from storageops.planners import (
    BranchAndBoundPlanner,
    ExhaustivePlanner,
    GreedyPlanner,
    Planner,
)

PlannerFactory = Callable[[], Planner]
Progress = Callable[[int, int, str], None]  # (done, total, instance name)

EXHAUSTIVE_MAX_FILES = 12


def default_planners(time_limit_s: float = 5.0) -> dict[str, PlannerFactory]:
    """The planners compared in the report, keyed by the label used in tables and figures."""
    return {
        "greedy": lambda: GreedyPlanner("ratio"),
        "greedy_heuristic": lambda: GreedyPlanner("heuristic"),
        "bnb": lambda: BranchAndBoundPlanner(time_limit_s=time_limit_s),
        "bnb_heuristic": lambda: BranchAndBoundPlanner(
            time_limit_s=time_limit_s, action_order="heuristic"
        ),
        "exhaustive": lambda: ExhaustivePlanner(max_files=EXHAUSTIVE_MAX_FILES),
    }


def run_benchmark(
    specs: Iterable[InstanceSpec],
    planners: Mapping[str, PlannerFactory] | None = None,
    *,
    config: Config | None = None,
    progress: Progress | None = None,
) -> pd.DataFrame:
    """Solve every instance with every planner.

    Exhaustive search is skipped on instances with more than
    ``EXHAUSTIVE_MAX_FILES`` candidates. Quality columns compare each plan with the
    cheapest goal plan any planner found on the same instance (``best_cost``),
    which is the true optimum whenever some planner proved optimality
    (``optimum_known``).
    """
    planners = planners or default_planners()
    specs = list(specs)
    rows = []
    for done, spec in enumerate(specs, start=1):
        instance = generate_instance(spec)
        problem = instance.problem(config)
        for label, factory in planners.items():
            planner = factory()
            if isinstance(planner, ExhaustivePlanner) and problem.n > planner.max_files:
                continue
            result = planner.plan(problem)
            plan, stats = result.plan, result.stats
            rows.append(
                {
                    "instance": spec.name,
                    "size_class": spec.size_class,
                    "n_files": spec.n_files,
                    "candidates": problem.n,
                    "duplicate_ratio": spec.duplicate_ratio,
                    "risk_level": spec.risk_level,
                    "target_fraction": spec.target_fraction,
                    "seed": spec.seed,
                    "required_bytes": problem.required_bytes,
                    "max_freeable_bytes": problem.max_freeable_bytes,
                    "planner": label,
                    "status": result.status,
                    "is_goal": plan.is_goal,
                    "freed_bytes": plan.freed_bytes,
                    "total_cost": plan.total_cost,
                    "actions": len(plan.actions),
                    "elapsed_s": stats.elapsed_s,
                    "nodes_generated": stats.nodes_generated,
                    "nodes_expanded": stats.nodes_expanded,
                    "nodes_pruned": stats.nodes_pruned,
                    "limit_reached": stats.limit_reached,
                    "proven_optimal": result.proven_optimal,
                    "lower_bound": result.lower_bound,
                    "gap": result.gap,
                }
            )
        if progress:
            progress(done, len(specs), spec.name)
    return add_quality_columns(pd.DataFrame(rows))


def add_quality_columns(results: pd.DataFrame) -> pd.DataFrame:
    """Add best_cost, optimum_known, excess_cost (cost / best - 1) and overshoot."""
    if results.empty:
        return results
    results = results.copy()
    goal_cost = results["total_cost"].where(results["is_goal"])
    results["best_cost"] = goal_cost.groupby(results["instance"]).transform("min")
    results["optimum_known"] = results.groupby("instance")["proven_optimal"].transform("any")
    with np.errstate(divide="ignore", invalid="ignore"):
        excess = goal_cost / results["best_cost"] - 1.0
    results["excess_cost"] = excess.where(results["best_cost"] > 0, 0.0).where(results["is_goal"])
    results["overshoot"] = (results["freed_bytes"] / results["required_bytes"] - 1.0).where(
        results["is_goal"] & (results["required_bytes"] > 0)
    )
    return results


def summarize(results: pd.DataFrame, by: str | list[str] = "size_class") -> pd.DataFrame:
    """Per group and planner: success rate, proven-optimal rate, quality, time and effort.

    ``mean_actions`` averages successful plans only.
    """
    keys = [by] if isinstance(by, str) else list(by)
    # Plan-shape statistics only make sense for plans that reach the target.
    results = results.assign(goal_actions=results["actions"].where(results["is_goal"]))
    grouped = results.groupby([*keys, "planner"], sort=False)
    summary = grouped.agg(
        instances=("instance", "nunique"),
        success_rate=("is_goal", "mean"),
        optimal_rate=("proven_optimal", "mean"),
        mean_excess_cost=("excess_cost", "mean"),
        max_excess_cost=("excess_cost", "max"),
        median_time_s=("elapsed_s", "median"),
        max_time_s=("elapsed_s", "max"),
        median_nodes=("nodes_generated", "median"),
        mean_actions=("goal_actions", "mean"),
        limit_rate=("limit_reached", "mean"),
    )
    return summary.reset_index()
