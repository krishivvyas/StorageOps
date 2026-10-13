import itertools
import math

import numpy as np
import pytest

from storageops.config import Config, SafetyConfig
from storageops.core.actions import ACTIONS
from storageops.core.analysis import analyze
from storageops.core.state import CleanupProblem, PlanState
from storageops.models import Action
from storageops.models import FileCategory as C
from tests.factories import make_frame, make_problem, make_record

INF = math.inf
K, D, A, Z, M = Action.KEEP, Action.DELETE, Action.ARCHIVE, Action.COMPRESS, Action.MOVE


# Columns: KEEP, DELETE, ARCHIVE, COMPRESS, MOVE
SMALL = make_problem(
    benefit=[[0, 100, 60, 30, 0], [0, 50, 0, 0, 0], [0, 80, 0, 0, 0]],
    cost=[[0, 0.5, 0.2, 0.1, INF], [0, 0.1, INF, INF, INF], [0, 0.3, INF, INF, INF]],
    groups=[-1, 0, 0],
    group_sizes=[2],  # f1 and f2 are the only copies of one file
    free=1_000,
    target=1_120,
)


def complete_states(problem):
    stack, out = [problem.initial_state()], []
    while stack:
        state = stack.pop()
        if problem.is_complete(state):
            out.append(state)
        else:
            stack.extend(child for _, child in problem.successors(state))
    return out


def test_basic_properties():
    assert SMALL.n == 3
    assert SMALL.required_bytes == 120
    assert SMALL.max_freeable_bytes == 230
    assert SMALL.state_space_log10 == pytest.approx(math.log10(4 * 2 * 2))
    assert SMALL.allowed_actions(0) == (K, D, A, Z)
    assert make_problem([[0, 1, 0, 0, 0]], [[0, 0, INF, INF, INF]], target=0).required_bytes == 0


def test_initial_state_and_successors():
    root = SMALL.initial_state()
    assert root == PlanState((), 0, 0.0, (0,))
    children = dict(SMALL.successors(root))
    assert list(children) == [K, D, A, Z]
    assert children[D].freed_bytes == 100 and children[D].cost == pytest.approx(0.5)
    assert children[A].actions == (A,)


def test_successor_enumeration_matches_brute_force():
    via_search = {s.actions for s in complete_states(SMALL)}
    brute = {
        combo
        for combo in itertools.product(ACTIONS, repeat=SMALL.n)
        if not SMALL.evaluate(combo).violations
    }
    assert via_search == brute
    assert len(via_search) == 4 * 2 * 2 - 4  # minus combos deleting both copies


def test_duplicate_group_cannot_lose_every_copy():
    for state in complete_states(SMALL):
        assert state.actions[1:] != (D, D)
    evaluation = SMALL.evaluate([K, D, D])
    assert evaluation.violations and not evaluation.is_goal


def test_goal_test():
    state = SMALL.initial_state()
    assert not SMALL.is_goal(state)
    goal = [s for s in complete_states(SMALL) if SMALL.is_goal(s)]
    assert goal and all(s.freed_bytes >= 120 for s in goal)
    # The cheapest goal: DELETE f0 (100) + DELETE f1 (50) = 0.6, or ARCHIVE f0 + DELETE f2 = 0.5
    best = min(goal, key=lambda s: s.cost)
    assert best.actions == (A, K, D) and best.cost == pytest.approx(0.5)


def test_partial_state_is_a_goal_when_target_already_met():
    _, child = next((a, c) for a, c in SMALL.successors(SMALL.initial_state()) if a is D)
    grandchild = next(c for a, c in SMALL.successors(child) if a is D)
    assert SMALL.is_goal(grandchild) and not SMALL.is_complete(grandchild)


def test_risk_budget_prunes_successors():
    tight = SMALL.permuted([0, 1, 2])
    tight = make_problem(
        tight.benefit, tight.cost, tight.group_index, tight.group_sizes,
        free=1_000, target=1_120, budget=0.25,
    )  # fmt: skip
    assert [a for a, _ in tight.successors(tight.initial_state())] == [K, A, Z]
    assert all(s.cost <= 0.25 for s in complete_states(tight))


def test_evaluate_reports_disallowed_actions():
    evaluation = SMALL.evaluate([M])
    assert any("MOVE is not allowed" in v for v in evaluation.violations)


def test_assignments_accept_prefixes_and_mappings():
    assert SMALL.evaluate([A]).freed_bytes == 60
    assert SMALL.evaluate({2: D}).freed_bytes == 80
    with pytest.raises(ValueError):
        SMALL.evaluate([K, K, K, K])
    with pytest.raises(IndexError):
        SMALL.evaluate({5: D})


def test_to_plan_lists_only_non_keep_actions():
    plan = SMALL.to_plan([A, K, D])
    assert [(a.path, a.action) for a in plan.actions] == [("f0", A), ("f2", D)]
    assert plan.freed_bytes == 140 and plan.total_cost == pytest.approx(0.5)
    assert plan.meets_target and plan.is_goal
    assert plan.count(D) == 1 and plan.freed_by(A) == 60
    state = next(s for s in complete_states(SMALL) if s.actions == (A, K, D))
    assert SMALL.to_plan(state) == plan


def test_permuted_problem_is_equivalent():
    order = [2, 0, 1]
    permuted = SMALL.permuted(order)
    assert permuted.paths == ("f2", "f0", "f1")
    original = SMALL.evaluate([A, K, D])
    reordered = permuted.evaluate([D, A, K])
    assert (original.freed_bytes, original.total_cost) == (
        reordered.freed_bytes,
        reordered.total_cost,
    )
    assert len(complete_states(permuted)) == len(complete_states(SMALL))
    with pytest.raises(ValueError):
        SMALL.permuted([0, 0, 1])


def test_invalid_shapes_and_negative_costs_are_rejected():
    with pytest.raises(ValueError):
        make_problem([[0, 1, 0, 0]], [[0, 0, 0, 0]])
    with pytest.raises(ValueError):
        make_problem([[0, 1, 0, 0, 0]], [[0, -1, INF, INF, INF]])


def test_from_analysis():
    frame = make_frame(
        make_record(r"C:\d\small.log", size=1_000, category=C.LOG, age_days=400),
        make_record(r"C:\d\big.tmp", size=50_000, category=C.TEMPORARY, age_days=400),
        make_record(r"C:\d\copy1.zip", size=9_000, category=C.COMPRESSED, duplicate_group=1),
        make_record(r"C:\d\copy2.zip", size=9_000, category=C.COMPRESSED, duplicate_group=1),
        make_record(r"C:\d\driver.sys", size=99_999, category=C.SYSTEM),
    )
    config = Config(safety=SafetyConfig(protected_paths=[]))
    analysis = analyze(frame, config)
    problem = CleanupProblem.from_analysis(analysis, free_bytes=100, target_free_bytes=40_100)

    assert problem.excluded_files == 1  # driver.sys is protected
    assert problem.paths == (r"C:\d\big.tmp", r"C:\d\copy1.zip", r"C:\d\copy2.zip",
                             r"C:\d\small.log")  # fmt: skip
    assert problem.group_index.tolist() == [-1, 0, 0, -1]
    assert problem.group_sizes == (2,)
    assert problem.required_bytes == 40_000
    np.testing.assert_array_equal(problem.benefit[0], analysis.tables.benefit.iloc[1])


def test_protected_copy_lets_every_candidate_copy_be_deleted():
    frame = make_frame(
        make_record(r"C:\d\a.zip", size=500, category=C.COMPRESSED, duplicate_group=7),
        make_record(r"C:\d\b.zip", size=500, category=C.COMPRESSED, duplicate_group=7),
        make_record(r"C:\Safe\c.zip", size=500, category=C.COMPRESSED, duplicate_group=7),
    )
    config = Config(safety=SafetyConfig(protected_paths=[r"C:\Safe"]))
    problem = CleanupProblem.from_analysis(
        analyze(frame, config), free_bytes=0, target_free_bytes=1_000
    )
    assert problem.n == 2 and problem.group_sizes == (3,)
    assert not problem.evaluate([D, D]).violations  # the protected copy survives
