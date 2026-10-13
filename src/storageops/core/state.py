"""State-space formulation of the cleanup problem.

Problem instance (CleanupProblem)
    n candidate files f_0..f_(n-1) in a fixed decision order, the action matrices
    b (benefit) and c (cost, +inf = disallowed), current free space F0, target T,
    and an optional risk budget C_max.

State (PlanState)
    A partial plan: actions chosen for f_0..f_(k-1). Every undecided file is
    implicitly KEPT, so every state is also a complete, executable plan. The
    state also stores bytes freed B_k, path cost C_k and per-group DELETE counts.

Initial state
    k = 0, nothing assigned, B = 0, C = 0.

Successors
    Assign one allowed action to f_k. A child is pruned when it would
    (a) DELETE the last surviving copy of a duplicate group, or (b) exceed C_max.
    Costs are non-negative, so (b) can never discard a goal.

Goal test
    F0 + B_k >= T and C_k <= C_max (constraints hold by construction).

Path cost
    C_k = sum of c(f_i, a_i) for i < k.

The state space has prod |A(f_i)| complete assignments, up to 5 ** n.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from storageops.core.actions import ACTION_INDEX, ACTIONS
from storageops.core.analysis import StorageAnalysis
from storageops.models import Action, CleanupPlan, PlannedAction

KEEP = ACTION_INDEX[Action.KEEP]
DELETE = ACTION_INDEX[Action.DELETE]

Assignment = Sequence[Action] | Mapping[int, Action]
"""Either one action per file (a prefix is fine; the rest are KEPT) or a sparse {file: action}."""


@dataclass(frozen=True, slots=True)
class PlanState:
    assignment: tuple[int, ...]  # action indices (see core.actions.ACTIONS) for f_0..f_(k-1)
    freed_bytes: int
    cost: float
    group_deletes: tuple[int, ...]  # DELETEs chosen so far in each duplicate group

    @property
    def depth(self) -> int:
        return len(self.assignment)

    @property
    def actions(self) -> tuple[Action, ...]:
        return tuple(ACTIONS[i] for i in self.assignment)


@dataclass(frozen=True)
class PlanEvaluation:
    freed_bytes: int
    total_cost: float
    meets_target: bool
    within_budget: bool
    violations: tuple[str, ...]

    @property
    def is_goal(self) -> bool:
        return self.meets_target and self.within_budget and not self.violations


@dataclass(frozen=True, eq=False)
class CleanupProblem:
    paths: tuple[str, ...]
    sizes: np.ndarray  # (n,) int64
    risk: np.ndarray  # (n,) float
    benefit: np.ndarray  # (n, |A|) int64
    cost: np.ndarray  # (n, |A|) float, +inf = disallowed
    heuristic: np.ndarray  # (n, |A|) float, -inf = disallowed
    group_index: np.ndarray  # (n,) int, -1 = not in a duplicate group
    group_sizes: tuple[int, ...]  # copies per group, counting non-candidate files too
    free_bytes: int
    target_free_bytes: int
    risk_budget: float = math.inf
    excluded_files: int = 0  # files outside the search (protected / nothing allowed)
    _allowed: np.ndarray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        n = len(self.paths)
        for name in ("sizes", "risk", "group_index"):
            if getattr(self, name).shape != (n,):
                raise ValueError(f"{name} must have shape ({n},)")
        for name in ("benefit", "cost", "heuristic"):
            if getattr(self, name).shape != (n, len(ACTIONS)):
                raise ValueError(f"{name} must have shape ({n}, {len(ACTIONS)})")
        if np.any(self.cost < 0):
            raise ValueError("action costs must be non-negative")
        object.__setattr__(self, "_allowed", np.isfinite(self.cost))

    # ------------------------------------------------------------------ construction

    @classmethod
    def from_analysis(
        cls,
        analysis: StorageAnalysis,
        *,
        free_bytes: int,
        target_free_bytes: int,
        risk_budget: float = math.inf,
    ) -> CleanupProblem:
        """Turn an analysis into a search problem.

        Files with only KEEP allowed become fixed context, not decision variables.
        Candidates are ordered by size, largest first (a good default for
        branch-and-bound); use :meth:`permuted` to change the order.
        """
        files = analysis.files
        candidates = analysis.candidates.to_numpy()
        order = np.flatnonzero(candidates)
        order = order[np.argsort(-files["size_bytes"].to_numpy()[order], kind="stable")]

        groups = files["duplicate_group"]
        counts = groups.value_counts()
        labels = list(counts.index)
        label_to_index = {label: i for i, label in enumerate(labels)}
        group_index = np.array(
            [label_to_index[g] if pd.notna(g) else -1 for g in groups.to_numpy()[order]],
            dtype=np.int64,
        )

        return cls(
            paths=tuple(files["path"].to_numpy()[order].astype(str)),
            sizes=files["size_bytes"].to_numpy(dtype=np.int64)[order],
            risk=analysis.risk["risk"].to_numpy(dtype=float)[order],
            benefit=analysis.tables.benefit.to_numpy(dtype=np.int64)[order],
            cost=analysis.tables.cost.to_numpy(dtype=float)[order],
            heuristic=analysis.heuristic.to_numpy(dtype=float)[order],
            group_index=group_index,
            group_sizes=tuple(int(counts[label]) for label in labels),
            free_bytes=int(free_bytes),
            target_free_bytes=int(target_free_bytes),
            risk_budget=risk_budget,
            excluded_files=int(len(files) - len(order)),
        )

    def permuted(self, order: Sequence[int]) -> CleanupProblem:
        """The same problem with decision variables in a different order."""
        order = np.asarray(order, dtype=np.int64)
        if sorted(order.tolist()) != list(range(self.n)):
            raise ValueError("order must be a permutation of range(n)")
        return CleanupProblem(
            paths=tuple(self.paths[i] for i in order),
            sizes=self.sizes[order],
            risk=self.risk[order],
            benefit=self.benefit[order],
            cost=self.cost[order],
            heuristic=self.heuristic[order],
            group_index=self.group_index[order],
            group_sizes=self.group_sizes,
            free_bytes=self.free_bytes,
            target_free_bytes=self.target_free_bytes,
            risk_budget=self.risk_budget,
            excluded_files=self.excluded_files,
        )

    def keeping(self, paths: Collection[str]) -> CleanupProblem:
        """The same problem with the given files forced to KEEP (e.g. rejected by the user)."""
        fixed = np.isin(np.asarray(self.paths, dtype=object), list(paths))
        cost = self.cost.copy()
        heuristic = self.heuristic.copy()
        cost[fixed, KEEP + 1 :] = math.inf
        heuristic[fixed, KEEP + 1 :] = -math.inf
        return replace(self, cost=cost, heuristic=heuristic)

    # ------------------------------------------------------------------ properties

    @property
    def n(self) -> int:
        return len(self.paths)

    @property
    def required_bytes(self) -> int:
        """Bytes the plan must free: max(0, T - F0)."""
        return max(0, self.target_free_bytes - self.free_bytes)

    @property
    def allowed(self) -> np.ndarray:
        return self._allowed

    @property
    def max_freeable_bytes(self) -> int:
        """Upper bound on achievable benefit (ignores the duplicate constraint)."""
        if self.n == 0:
            return 0
        return int(np.where(self._allowed, self.benefit, 0).max(axis=1).sum())

    @property
    def state_space_log10(self) -> float:
        """log10 of the number of complete assignments."""
        return float(np.log10(self._allowed.sum(axis=1)).sum()) if self.n else 0.0

    def allowed_actions(self, i: int) -> tuple[Action, ...]:
        return tuple(ACTIONS[j] for j in np.flatnonzero(self._allowed[i]))

    # ------------------------------------------------------------------ search interface

    def initial_state(self) -> PlanState:
        return PlanState((), 0, 0.0, (0,) * len(self.group_sizes))

    def is_complete(self, state: PlanState) -> bool:
        return state.depth == self.n

    def is_goal(self, state: PlanState) -> bool:
        return state.freed_bytes >= self.required_bytes and state.cost <= self.risk_budget

    def successors(self, state: PlanState) -> Iterator[tuple[Action, PlanState]]:
        """Children from assigning each allowed action to the next undecided file."""
        k = state.depth
        if k >= self.n:
            return
        group = int(self.group_index[k])
        for j in np.flatnonzero(self._allowed[k]):
            j = int(j)
            cost = state.cost + float(self.cost[k, j])
            if cost > self.risk_budget:
                continue
            deletes = state.group_deletes
            if j == DELETE and group >= 0:
                if deletes[group] + 1 >= self.group_sizes[group]:
                    continue  # would delete every copy
                deletes = deletes[:group] + (deletes[group] + 1,) + deletes[group + 1 :]
            child = PlanState(
                state.assignment + (j,),
                state.freed_bytes + int(self.benefit[k, j]),
                cost,
                deletes,
            )
            yield ACTIONS[j], child

    # ------------------------------------------------------------------ evaluation

    def evaluate(self, assignment: Assignment) -> PlanEvaluation:
        """Check any (possibly hand-made) plan against every constraint."""
        indices = self._indices(assignment)
        violations: list[str] = []
        freed, cost = 0, 0.0
        deletes = [0] * len(self.group_sizes)
        for i, j in enumerate(indices):
            if not self._allowed[i, j]:
                violations.append(f"{ACTIONS[j].value} is not allowed for {self.paths[i]}")
                continue
            freed += int(self.benefit[i, j])
            cost += float(self.cost[i, j])
            if j == DELETE and self.group_index[i] >= 0:
                deletes[self.group_index[i]] += 1
        for g, count in enumerate(deletes):
            if count and count >= self.group_sizes[g]:
                violations.append(f"every copy of duplicate group #{g} would be deleted")
        return PlanEvaluation(
            freed_bytes=freed,
            total_cost=cost,
            meets_target=freed >= self.required_bytes,
            within_budget=cost <= self.risk_budget,
            violations=tuple(violations),
        )

    def to_plan(self, assignment: Assignment | PlanState) -> CleanupPlan:
        if isinstance(assignment, PlanState):
            assignment = assignment.actions
        indices = self._indices(assignment)
        evaluation = self.evaluate(assignment)
        actions = tuple(
            PlannedAction(
                path=self.paths[i],
                action=ACTIONS[j],
                size_bytes=int(self.sizes[i]),
                benefit_bytes=int(self.benefit[i, j]),
                cost=float(self.cost[i, j]),
                risk=float(self.risk[i]),
            )
            for i, j in enumerate(indices)
            if j != KEEP
        )
        return CleanupPlan(
            actions=actions,
            required_bytes=self.required_bytes,
            freed_bytes=evaluation.freed_bytes,
            total_cost=evaluation.total_cost,
            meets_target=evaluation.meets_target,
            is_goal=evaluation.is_goal,
        )

    def _indices(self, assignment: Assignment) -> list[int]:
        indices = [KEEP] * self.n
        if isinstance(assignment, Mapping):
            items = assignment.items()
        else:
            if len(assignment) > self.n:
                raise ValueError(f"assignment has {len(assignment)} entries for {self.n} files")
            items = enumerate(assignment)
        for i, action in items:
            if not 0 <= i < self.n:
                raise IndexError(f"file index {i} out of range")
            indices[i] = ACTION_INDEX[Action(action)]
        return indices
