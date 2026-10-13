import math

import numpy as np
import pytest

from storageops.planners import ExhaustivePlanner
from storageops.planners.relaxation import Relaxation, lower_hull
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
    bound = Relaxation(problem.benefit, problem.cost).bound(0, problem.required_bytes)
    if optimum.proven_infeasible:
        return
    assert bound <= optimum.plan.total_cost + 1e-9
