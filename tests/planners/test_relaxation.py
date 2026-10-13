import itertools
import math

import numpy as np
import pytest

from storageops.planners import BranchAndBoundPlanner, ExhaustivePlanner
from storageops.planners.relaxation import Relaxation, file_hull, group_chain, lower_hull
from tests.factories import random_problem

INF = math.inf


def test_lower_hull_drops_dominated_and_non_convex_points():
    # (60, 0.6) lies above the segment origin -> (100, 0.5): it is dominated
    assert lower_hull([(60, 0.6, 2), (100, 0.5, 1)]) == [(0, 0.0, -1), (100, 0.5, 1)]
    # convex chain: cheap small step, then a steeper big step
    hull = lower_hull([(24, 0.08, 3), (40, 0.24, 2), (100, 1.0, 1)])
    assert [v[2] for v in hull] == [-1, 3, 2, 1]
    slopes = [
        (c1 - c0) / (b1 - b0) for (b0, c0, _), (b1, c1, _) in zip(hull, hull[1:], strict=False)
    ]
    assert slopes == sorted(slopes)


def test_lower_hull_keeps_cheapest_of_equal_benefit_and_ignores_zero_benefit():
    assert lower_hull([(50, 0.9, 1), (50, 0.3, 4), (0, 0.0, 2)]) == [(0, 0.0, -1), (50, 0.3, 4)]


def test_bound_fills_cheapest_bytes_first_fractionally():
    benefit = np.array([[0, 100, 0, 0, 0], [0, 50, 0, 0, 0]])
    cost = np.array([[0, 1.0, INF, INF, INF], [0, 0.25, INF, INF, INF]])
    relaxation = Relaxation(benefit, cost)
    assert relaxation.bound(0, 0) == 0.0
    assert relaxation.bound(0, 50) == pytest.approx(0.25)  # all of file 1
    assert relaxation.bound(0, 100) == pytest.approx(0.25 + 0.5)  # plus half of file 0
    assert relaxation.bound(1, 50) == pytest.approx(0.25)
    assert relaxation.bound(1, 51) == math.inf  # file 1 alone cannot cover it
    assert relaxation.bound(0, 151) == math.inf


@pytest.mark.parametrize("seed", range(30))
def test_bound_never_exceeds_the_true_optimum(seed):
    problem = random_problem(np.random.default_rng(seed), 8)
    optimum = ExhaustivePlanner().plan(problem)
    relaxation = Relaxation(problem.benefit, problem.cost, problem.group_index, problem.group_sizes)
    bound = relaxation.bound(0, problem.required_bytes)
    if optimum.proven_infeasible:
        return
    assert bound <= optimum.plan.total_cost + 1e-9


def test_group_chain_is_below_every_feasible_group_assignment_and_tight():
    # three copies of a 100-byte file; DELETE is cheap, ARCHIVE frees 40
    benefit = np.array([[0, 100, 40, 0, 0]] * 3)
    cost = np.array([[0, 0.1, 0.3, INF, INF], [0, 0.2, 0.3, INF, INF], [0, 0.4, 0.3, INF, INF]])
    hulls = [file_hull(benefit[i], cost[i]) for i in range(3)]
    capped = cost.copy()
    capped[:, 1] = INF
    no_delete = [file_hull(benefit[i], capped[i]) for i in range(3)]
    chain = group_chain(hulls, no_delete)

    def chain_cost(b):  # piecewise-linear interpolation of the chain
        for (b0, c0, _), (b1, c1, _) in zip(chain, chain[1:], strict=False):
            if b0 <= b <= b1:
                return c0 + (c1 - c0) * (b - b0) / (b1 - b0)
        return INF

    feasible = []
    for combo in itertools.product([0, 1, 2], repeat=3):  # KEEP, DELETE, ARCHIVE
        if combo.count(1) == 3:
            continue  # all copies deleted: forbidden
        feasible.append(
            (
                sum(benefit[i, j] for i, j in enumerate(combo)),
                sum(cost[i, j] for i, j in enumerate(combo)),
            )
        )
    for b, c in feasible:
        assert chain_cost(b) <= c + 1e-12
    assert max(v[0] for v in chain) == 240  # two deletes + one archive, never 300
    for b, c, _ in chain[1:]:
        assert any(abs(b - fb) < 1e-9 and abs(c - fc) < 1e-9 for fb, fc in feasible)


def test_group_bound_is_tighter_than_ignoring_the_constraint():
    benefit = np.array([[0, 100, 0, 0, 0]] * 2)
    cost = np.array([[0, 0.1, INF, INF, INF]] * 2)
    loose = Relaxation(benefit, cost)
    tight = Relaxation(benefit, cost, np.array([0, 0]), [2])
    assert loose.bound(0, 200) == pytest.approx(0.2)
    assert tight.bound(0, 200) == INF  # deleting both copies is impossible
    assert tight.bound(1, 100) == pytest.approx(0.1)  # inside the group: per-file fallback


@pytest.mark.parametrize("seed", range(15))
@pytest.mark.parametrize("group_size", [2, 3])
@pytest.mark.parametrize("shuffle", [False, True])
def test_bnb_with_larger_and_scattered_groups_matches_exhaustive(seed, group_size, shuffle):
    problem = random_problem(
        np.random.default_rng(seed), 8, group_size=group_size, shuffle=shuffle,
        target_fraction=0.6,
    )  # fmt: skip
    truth = ExhaustivePlanner().plan(problem)
    for action_order in ("bound", "heuristic"):
        for order in ("ratio", "benefit", "problem"):
            result = BranchAndBoundPlanner(order=order, action_order=action_order).plan(problem)
            assert result.proven_infeasible == truth.proven_infeasible
            if not truth.proven_infeasible:
                assert result.plan.total_cost == pytest.approx(truth.plan.total_cost)
