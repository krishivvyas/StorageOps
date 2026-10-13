"""The review-and-approve workflow, independent of any GUI toolkit.

The tkinter app (``gui/app.py``) only displays a :class:`Session` and forwards
clicks to it. Every rule of the workflow lives here, where it can be tested headlessly:

1. load: scan a folder or open a saved dataset (read-only);
2. plan: analyse, then search for a plan reaching the free-space target;
3. review: every proposed action starts **unapproved**; the user approves
   actions one by one or in bulk, and may reject files and re-plan without them;
4. dry run: required before execution, for exactly the current selection;
5. execute: only approved actions, through the safety-checked executor; the
   session then asks for a re-scan, because the plan no longer matches the disk.

**Nothing happens without the user's OK.** Every step that does work (load/scan,
plan, re-plan, dry run, execute) takes a required ``user_ok`` argument. The UI
passes ``True`` only after the user confirmed a dialog or prompt describing the
step. Anything else is refused and logged.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pandas as pd

from storageops.config import Config
from storageops.core import CleanupProblem, StorageAnalysis, analyze
from storageops.executor import ExecutionReport, Executor, Journal, approve
from storageops.executor.executor import ApprovedAction
from storageops.models import Action, PlannedAction
from storageops.paths import journal_dir as default_journal_dir
from storageops.planners import PlannerResult, make_planner
from storageops.sensors import EnvironmentSnapshot, capture_environment

TargetMode = Literal["free", "target"]
log = logging.getLogger(__name__)
Progress = Callable[[int, int, str], None]


class WorkflowError(RuntimeError):
    """An action was requested out of order (e.g. execute before a dry run)."""


@dataclass(frozen=True)
class ApprovalSummary:
    actions: int
    bytes: int  # expected bytes freed by the approved actions
    required: int
    by_action: dict[str, int]

    @property
    def shortfall(self) -> int:
        return max(0, self.required - self.bytes)


class Session:
    def __init__(
        self,
        config: Config | None = None,
        *,
        journal_dir: Path | None = None,
        trash: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config or Config()
        self.trash = trash  # None = the real Recycle Bin (send2trash)
        self.journal_dir = Path(journal_dir) if journal_dir else default_journal_dir()
        self.snapshot: EnvironmentSnapshot | None = None
        self.analysis: StorageAnalysis | None = None
        self.problem: CleanupProblem | None = None
        self.result: PlannerResult | None = None
        self.approved: set[str] = set()
        self.rejected: set[str] = set()
        self.last_report: ExecutionReport | None = None
        self.needs_rescan = False
        self._move_destination: str | None = None
        self._plan_args: dict | None = None
        self._dry_run_signature: str | None = None

    # ------------------------------------------------------------------ 1. load

    def load(
        self,
        source: str | Path,
        *,
        user_ok: bool,
        progress: Callable[[int, str], None] | None = None,
    ) -> None:
        """Scan a folder, or open a dataset saved by ``storageops scan -o``."""
        _require_ok(user_ok, f"load {source}")
        source = Path(source)
        if source.is_file():
            snapshot = EnvironmentSnapshot.load(source)
        else:
            snapshot = capture_environment(source, self.config, progress=progress)
        self._reset()
        self.snapshot = snapshot
        kind = "saved dataset" if source.is_file() else "fresh scan"
        log.info("loaded %s: %d files (%s)", source, len(snapshot.files), kind)

    def _reset(self) -> None:
        self.analysis = self.problem = self.result = None
        self.approved.clear()
        self.rejected.clear()
        self.last_report = None
        self.needs_rescan = False
        self._plan_args = None
        self._dry_run_signature = None

    # ------------------------------------------------------------------ 2. plan

    def plan(
        self,
        target_bytes: int,
        *,
        mode: TargetMode = "free",
        planner: str = "bnb",
        time_limit_s: float = 10.0,
        move_destination: str | None = None,
        user_ok: bool,
    ) -> PlannerResult:
        _require_ok(user_ok, "plan")
        snapshot = self._require_snapshot()
        move_destination = move_destination or None
        self.ensure_analysis(move_destination)
        free = snapshot.disk.free_bytes
        target_free = free + target_bytes if mode == "free" else target_bytes
        problem = CleanupProblem.from_analysis(
            self.analysis, free_bytes=free, target_free_bytes=target_free
        ).keeping(self.rejected)
        self.problem = problem
        self.result = make_planner(planner, time_limit_s=time_limit_s).plan(problem)
        self._plan_args = {
            "target_bytes": target_bytes, "mode": mode, "planner": planner,
            "time_limit_s": time_limit_s, "move_destination": move_destination,
        }  # fmt: skip
        self.approved.clear()  # a new plan always starts unapproved
        self._dry_run_signature = None
        plan = self.result.plan
        log.info(
            "plan: %s %d bytes, planner=%s, move_to=%s, rejected=%d -> %s, %d actions, "
            "frees %d bytes, cost %.4f",
            mode, target_bytes, planner, move_destination, len(self.rejected),
            self.result.status, len(plan.actions), plan.freed_bytes, plan.total_cost,
        )  # fmt: skip
        return self.result

    def ensure_analysis(self, move_destination: str | None = None) -> StorageAnalysis:
        """Risk/safety/action analysis of the loaded files (recomputed if MOVE changes)."""
        snapshot = self._require_snapshot()
        move_destination = move_destination or None
        if self.analysis is None or move_destination != self._move_destination:
            self.analysis = analyze(snapshot.files, self.config, move_destination=move_destination)
            self._move_destination = move_destination
        return self.analysis

    def replan_without(self, paths: Iterable[str], *, user_ok: bool) -> PlannerResult:
        """Reject files (force KEEP) and search again for a plan that avoids them."""
        _require_ok(user_ok, "re-plan")
        if self._plan_args is None:
            raise WorkflowError("make a plan first")
        paths = set(paths)
        log.info("user rejected %d file(s): %s", len(paths), sorted(paths))
        self.rejected |= paths
        return self.plan(**self._plan_args, user_ok=True)

    def clear_rejections(self) -> None:
        self.rejected.clear()

    # ------------------------------------------------------------------ 3. review

    @property
    def proposed(self) -> tuple[PlannedAction, ...]:
        return self.result.plan.actions if self.result else ()

    def set_approved(self, paths: Iterable[str], approved: bool = True) -> None:
        valid = {a.path for a in self.proposed}
        paths = set(paths) & valid
        if approved:
            self.approved |= paths
        else:
            self.approved -= paths
        if paths:
            verb = "approved" if approved else "unapproved"
            log.info("user %s %d action(s); %d approved in total", verb, len(paths),
                     len(self.approved))  # fmt: skip

    def approve_all(self) -> None:
        self.set_approved(a.path for a in self.proposed)

    def approve_none(self) -> None:
        self.approved.clear()

    def approval_summary(self) -> ApprovalSummary:
        chosen = [a for a in self.proposed if a.path in self.approved]
        return ApprovalSummary(
            actions=len(chosen),
            bytes=sum(a.benefit_bytes for a in chosen),
            required=self.problem.required_bytes if self.problem else 0,
            by_action=dict(Counter(a.action.value for a in chosen)),
        )

    # ------------------------------------------------------------------ 4./5. dry run, execute

    @property
    def can_execute(self) -> bool:
        """True only after a dry run of exactly the current selection."""
        return (
            not self.needs_rescan
            and bool(self.approved)
            and self._dry_run_signature == _signature(self._approved_actions())
        )

    def execute(
        self,
        *,
        dry_run: bool,
        user_ok: bool,
        progress: Progress | None = None,
        cancel: threading.Event | None = None,
        trash: Callable[[str], None] | None = None,
    ) -> ExecutionReport:
        _require_ok(user_ok, "dry run" if dry_run else "execution")
        if self.needs_rescan:
            raise WorkflowError("files were changed: re-scan before planning again")
        actions = self._approved_actions()
        if not actions:
            raise WorkflowError("approve at least one action first")
        if not dry_run and not self.can_execute:
            raise WorkflowError("run a dry run of exactly this selection before executing")
        journal = Journal(self.journal_dir / _journal_name(dry_run))
        executor = Executor(
            dry_run=dry_run,
            journal=journal,
            move_destination=self._move_destination,
            safety=self.config.safety,
            trash=trash or self.trash,
        )
        what = "dry run" if dry_run else "EXECUTION"
        log.info("%s of %d approved action(s) started; journal %s", what, len(actions),
                 journal.path)  # fmt: skip
        report = executor.run(actions, progress=progress, cancel=cancel)
        self.last_report = report
        log.info("%s finished: %s, %d bytes %s%s", what, dict(report.counts()),
                 report.bytes_freed, "expected" if dry_run else "freed",
                 " (cancelled)" if report.cancelled else "")  # fmt: skip
        if dry_run:
            self._dry_run_signature = _signature(actions)
        else:
            self.needs_rescan = True
            self.approved.clear()
            self._dry_run_signature = None
        return report

    def _approved_actions(self) -> list[ApprovedAction]:
        if self.result is None or self.snapshot is None:
            return []
        return approve(self.result.plan, self.snapshot.files, self.approved)

    def _require_snapshot(self) -> EnvironmentSnapshot:
        if self.snapshot is None:
            raise WorkflowError("scan a folder or open a dataset first")
        return self.snapshot

    # ------------------------------------------------------------------ views

    def plan_table(self) -> pd.DataFrame:
        """Proposed actions with approval state, for display."""
        rows = [
            {
                "approved": a.path in self.approved,
                "action": a.action.value,
                "path": a.path,
                "size_bytes": a.size_bytes,
                "benefit_bytes": a.benefit_bytes,
                "risk": a.risk,
                "cost": a.cost,
            }
            for a in self.proposed
        ]
        columns = ["approved", "action", "path", "size_bytes", "benefit_bytes", "risk", "cost"]
        return pd.DataFrame(rows, columns=columns)


def _require_ok(user_ok: bool, step: str) -> None:
    if user_ok is not True:
        log.warning("refused %s: the user has not confirmed it", step)
        raise WorkflowError(f"{step} needs the user's OK first")
    log.info("user confirmed: %s", step)


def _signature(actions: list[ApprovedAction]) -> str:
    text = "\n".join(sorted(f"{a.action.value}|{a.path}|{a.size_bytes}" for a in actions))
    return hashlib.blake2b(text.encode(), digest_size=16).hexdigest()


def _journal_name(dry_run: bool) -> str:
    from datetime import datetime

    return f"{datetime.now():%Y%m%d-%H%M%S-%f}{'-dryrun' if dry_run else ''}.jsonl"


def describe_action(action: Action) -> str:
    return {
        Action.DELETE: "send to Recycle Bin",
        Action.ARCHIVE: "zip, verify, remove original",
        Action.COMPRESS: "NTFS compress in place",
        Action.MOVE: "move to other drive",
        Action.KEEP: "keep",
    }[action]
