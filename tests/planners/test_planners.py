import functools
import math

import numpy as np
import pytest

from storageops.models import Action
from storageops.planners import (
    BranchAndBoundPlanner,
    ExhaustivePlanner,
    GreedyPlanner,
    plan_to_frame,
)
from tests.factories import make_problem, random_problem

INF = math.inf
K, D, A, Z, M = Action.KEEP, Action.DELETE, Action.ARCHIVE, Action.COMPRESS, Action.MOVE

ALL_PLANNERS = [
    GreedyPlanner(),
    GreedyPlanner("heuristic"),
    BranchAndBoundPlanner(),
    BranchAndBoundPlanner(action_order="heuristic"),
    ExhaustivePlanner(),
]

# Greedy trap: f0 has the best cost per byte but is huge, so taking it overshoots the
# target at a high absolute cost. The optimum is the two small files.
TRAP = make_problem(
    benefit=[[0, 1000, 0, 0, 0], [0, 60, 0, 0, 0], [0, 50, 0, 0, 0]],
    cost=[[0, 1.0, INF, INF, INF], [0, 0.2, INF, INF, INF], [0, 0.2, INF, INF, INF]],
    target=100,
)


@pytest.mark.parametrize("planner", ALL_PLANNERS, ids=lambda p: type(p).__name__)
def test_zero_requirement_gives_empty_plan(planner):
    problem = make_problem([[0, 10, 0, 0, 0]], [[0, 0.1, INF, INF, INF]], free=50, target=40)
    result = planner.plan(problem)
    assert result.plan.actions == () and result.plan.is_goal


@pytest.mark.parametrize("planner", ALL_PLANNERS, ids=lambda p: type(p).__name__)
def test_infeasible_target_is_never_reported_as_a_goal(planner):
    problem = make_problem([[0, 10, 0, 0, 0]], [[0, 0.1, INF, INF, INF]], target=11)
    result = planner.plan(problem)
    assert not result.plan.is_goal
    assert result.status in {"infeasible", "no plan found"}


def test_ratio_greedy_falls_into_the_trap_and_bnb_does_not():
    greedy = GreedyPlanner().plan(TRAP)
    assert greedy.assignment == (D, K, K) and greedy.plan.total_cost == pytest.approx(1.0)
    assert greedy.status == "feasible"

    bnb = BranchAndBoundPlanner().plan(TRAP)
    assert bnb.assignment == (K, D, D) and bnb.plan.total_cost == pytest.approx(0.4)
    assert bnb.status == "optimal" and bnb.gap == 0.0


def test_ratio_greedy_upgrades_along_the_hull():
    # COMPRESS is the cheapest per byte, then the upgrade to DELETE is the next cheapest.
    problem = make_problem(
        benefit=[[0, 1000, 0, 300, 0], [0, 900, 0, 0, 0]],
        cost=[[0, 0.5, INF, 0.03, INF], [0, 2.0, INF, INF, INF]],
        target=1000,
    )
    result = GreedyPlanner().plan(problem)
    assert result.assignment == (D, K)
    assert result.stats.nodes_expanded == 2  # KEEP->COMPRESS, then COMPRESS->DELETE


def test_heuristic_greedy_follows_the_heuristic_and_can_upgrade():
    problem = make_problem(
        benefit=[[0, 100, 0, 20, 0], [0, 100, 0, 0, 0]],
        cost=[[0, 0.5, INF, 0.05, INF], [0, 0.4, INF, INF, INF]],
        heuristic=[[0, 0.1, 0, 0.9, 0], [0, 0.5, 0, 0, 0]],
        target=150,
    )
    result = GreedyPlanner("heuristic").plan(problem)
    # order: f0 COMPRESS (0.9), f1 DELETE (0.5), then f0 upgraded to DELETE (0.1)
    assert result.assignment == (D, D)
    assert result.stats.nodes_expanded == 3


def test_greedy_removes_redundant_actions():
    problem = make_problem(
        benefit=[[0, 10, 0, 0, 0], [0, 100, 0, 0, 0]],
        cost=[[0, 0.001, INF, INF, INF], [0, 0.5, INF, INF, INF]],
        target=100,
    )
    pruned = GreedyPlanner().plan(problem)
    assert pruned.assignment == (K, D) and pruned.stats.nodes_pruned == 1
    unpruned = GreedyPlanner(prune_redundant=False).plan(problem)
    assert unpruned.assignment == (D, D)


@pytest.mark.parametrize("planner", ALL_PLANNERS, ids=lambda p: type(p).__name__)
def test_planners_respect_duplicate_groups_and_budget(planner):
    # f0 and f1 are the only two copies of one file: deleting both is forbidden.
    problem = make_problem(
        benefit=[[0, 100, 0, 0, 0], [0, 100, 0, 0, 0], [0, 100, 0, 0, 0]],
        cost=[[0, 0.1, INF, INF, INF], [0, 0.1, INF, INF, INF], [0, 0.3, INF, INF, INF]],
        groups=[0, 0, -1],
        group_sizes=[2],
        target=200,
        budget=0.45,
    )
    result = planner.plan(problem)
    assert result.plan.is_goal
    assert result.assignment[:2].count(D) == 1
    assert result.plan.total_cost <= 0.45


@functools.cache
def instance_with_truth(seed):
    """Random instance plus its exhaustive-search optimum, computed once per seed."""
    rng = np.random.default_rng(seed)
    budget = None if seed % 3 else float(rng.uniform(0.5, 3.0))
    problem = random_problem(rng, 8, budget=budget, target_fraction=rng.uniform(0.1, 0.9))
    return problem, ExhaustivePlanner().plan(problem)


@pytest.mark.parametrize("seed", range(60))
@pytest.mark.parametrize(
    "bnb",
    [
        BranchAndBoundPlanner(),
        BranchAndBoundPlanner(action_order="heuristic"),
        BranchAndBoundPlanner(order="benefit", greedy_incumbent=False),
        BranchAndBoundPlanner(order="problem", action_order="heuristic", greedy_incumbent=False),
    ],
    ids=["bound", "heuristic", "benefit-cold", "problem-heuristic-cold"],
)
def test_branch_and_bound_matches_exhaustive_search(seed, bnb):
    problem, truth = instance_with_truth(seed)
    result = bnb.plan(problem)
    assert result.proven_infeasible == truth.proven_infeasible
    if truth.proven_infeasible:
        return
    assert result.status == "optimal"
    assert result.plan.is_goal
    assert result.plan.total_cost == pytest.approx(truth.plan.total_cost)
    assert result.stats.nodes_generated <= truth.stats.nodes_generated


@pytest.mark.parametrize("seed", range(20))
def test_greedy_plans_are_valid_and_never_beat_the_optimum(seed):
    problem, optimum = instance_with_truth(seed)
    for key in ("ratio", "heuristic"):
        result = GreedyPlanner(key).plan(problem)
        assert not problem.evaluate(result.assignment).violations
        if result.plan.is_goal:
            assert result.plan.total_cost >= optimum.plan.total_cost - 1e-9


def test_limits_return_best_plan_so_far_with_a_gap():
    problem = random_problem(np.random.default_rng(7), 400)
    result = BranchAndBoundPlanner(node_limit=50).plan(problem)
    assert result.stats.limit_reached
    assert not result.proven_optimal
    assert result.plan.is_goal  # the greedy incumbent at least
    assert result.lower_bound <= result.plan.total_cost
    assert 0 <= result.gap < 1


def test_bnb_without_incumbent_or_time_finds_nothing_but_says_so():
    problem = random_problem(np.random.default_rng(3), 400)
    result = BranchAndBoundPlanner(node_limit=1, greedy_incumbent=False).plan(problem)
    assert result.stats.limit_reached
    assert not result.proven_infeasible


def test_exhaustive_refuses_large_instances():
    with pytest.raises(ValueError):
        ExhaustivePlanner(max_files=5).plan(random_problem(np.random.default_rng(0), 6))


def test_invalid_options():
    with pytest.raises(ValueError):
        GreedyPlanner("random")
    with pytest.raises(ValueError):
        BranchAndBoundPlanner(order="alphabetical")
    with pytest.raises(ValueError):
        BranchAndBoundPlanner(action_order="random")


def test_plan_to_frame():
    plan = BranchAndBoundPlanner().plan(TRAP).plan
    frame = plan_to_frame(plan)
    assert frame["path"].tolist() == ["f1", "f2"]
    assert frame["action"].tolist() == ["DELETE", "DELETE"]
    assert frame["benefit_bytes"].sum() == plan.freed_bytes
    assert plan_to_frame(GreedyPlanner().plan(make_problem([], [])).plan).empty
