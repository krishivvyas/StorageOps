"""Heuristic-weight sensitivity: how each weight of h(f, a) affects plan quality and search effort.

One-at-a-time design. Each weight is swept over ``values`` while the other three
stay at their defaults. For every setting this measures:

* ``greedy_heuristic`` excess cost over the instance optimum (plan quality), and
* ``bnb_heuristic`` (heuristic action order) nodes generated and whether it
  proved optimality within the limit (search effort).

Only h changes between settings: risk, costs and therefore the optimum stay
fixed. Quality is measured against one reference optimum per instance, found
with branch-and-bound using the bound order, which does not depend on h.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace

import pandas as pd

from storageops.benchmark.generator import generate_instance
from storageops.benchmark.profiles import InstanceSpec
from storageops.config import Config, HeuristicConfig
from storageops.core.analysis import StorageAnalysis
from storageops.core.heuristic import heuristic_scores
from storageops.planners import BranchAndBoundPlanner, GreedyPlanner

COLUMNS = [
    "instance", "weight", "value", "is_default", "reference_cost", "greedy_cost",
    "greedy_excess_cost", "greedy_actions", "bnb_nodes", "bnb_time_s", "bnb_proven_optimal",
]  # fmt: skip
WEIGHTS: tuple[str, ...] = (
    "weight_benefit",
    "weight_inactivity",
    "weight_duplicate",
    "weight_risk",
)


def weight_sensitivity(
    specs: Iterable[InstanceSpec],
    values: Sequence[float] = (0.0, 0.25, 0.5, 0.75, 1.0),
    *,
    config: Config | None = None,
    time_limit_s: float = 2.0,
    progress: Callable[[int, int, str], None] | None = None,
) -> pd.DataFrame:
    config = config or Config()
    specs = list(specs)
    rows = []
    for done, spec in enumerate(specs, start=1):
        instance = generate_instance(spec)
        analysis = instance.analyze(config)
        reference = BranchAndBoundPlanner(time_limit_s=time_limit_s * 5).plan(
            instance.problem(analysis=analysis)
        )
        if not reference.plan.is_goal:
            continue  # infeasible instances say nothing about the heuristic
        best = reference.plan.total_cost

        for weight in WEIGHTS:
            default = getattr(HeuristicConfig(), weight)
            for value in sorted({*values, default}):  # always include the default setting
                heuristic_config = replace(config.heuristic, **{weight: value})
                problem = instance.problem(
                    analysis=_with_heuristic(analysis, heuristic_config, config)
                )
                greedy = GreedyPlanner("heuristic").plan(problem)
                bnb = BranchAndBoundPlanner(
                    time_limit_s=time_limit_s, action_order="heuristic"
                ).plan(problem)
                rows.append(
                    {
                        "instance": spec.name,
                        "weight": weight,
                        "value": value,
                        "is_default": value == default,
                        "reference_cost": best,
                        "greedy_cost": greedy.plan.total_cost if greedy.plan.is_goal else None,
                        "greedy_excess_cost": (
                            greedy.plan.total_cost / best - 1.0 if greedy.plan.is_goal and best
                            else None
                        ),
                        "greedy_actions": len(greedy.plan.actions),
                        "bnb_nodes": bnb.stats.nodes_generated,
                        "bnb_time_s": bnb.stats.elapsed_s,
                        "bnb_proven_optimal": bnb.proven_optimal,
                    }
                )  # fmt: skip
        if progress:
            progress(done, len(specs), spec.name)
    return pd.DataFrame(rows, columns=COLUMNS)


def _with_heuristic(
    analysis: StorageAnalysis, heuristic_config: HeuristicConfig, config: Config
) -> StorageAnalysis:
    scores = heuristic_scores(
        analysis.files, analysis.risk["risk"], analysis.tables, heuristic_config, config.actions
    )
    return replace(analysis, heuristic=scores)
