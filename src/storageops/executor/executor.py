"""Execute an approved cleanup plan: dry run by default, preflight checks, journal everything.

Safety layers, in order:

1. Only :class:`ApprovedAction` objects are accepted. They are built by
   :func:`approve` from a plan and the user's explicit selection.
2. ``dry_run=True`` is the default: all checks run, nothing on disk changes.
3. Preflight checks for every file: it still exists and is unchanged since the
   scan, and it is not under a protected location and not a system file
   (defence in depth: the planner already excludes these).
4. Each operation verifies its result before removing the original (see operations.py).
5. Every outcome, including refusals and failures, goes to the journal.
"""

from __future__ import annotations

import logging
import os
import stat
import threading
from collections import Counter
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

from storageops.config import SafetyConfig
from storageops.core.safety import protected_prefixes
from storageops.executor import operations as ops
from storageops.executor.journal import Journal
from storageops.models import Action, CleanupPlan

ApprovedAction = ops.ApprovedAction
Status = Literal["done", "dry-run", "skipped", "failed"]
log = logging.getLogger(__name__)
Progress = Callable[[int, int, str], None]


@dataclass(frozen=True)
class OperationResult:
    path: str
    action: Action
    status: Status
    message: str
    bytes_freed: int = 0
    artifact: str | None = None


@dataclass
class ExecutionReport:
    dry_run: bool
    results: list[OperationResult] = field(default_factory=list)
    cancelled: bool = False
    journal_path: str | None = None

    @property
    def bytes_freed(self) -> int:
        return sum(r.bytes_freed for r in self.results)

    def counts(self) -> Counter[str]:
        return Counter(r.status for r in self.results)


def approve(
    plan: CleanupPlan, files: pd.DataFrame, selected_paths: Collection[str]
) -> list[ApprovedAction]:
    """Turn the user's selection into executable actions.

    ``files`` is the scanned file table; it supplies each file's modification
    time, so the executor can detect files that changed after planning.
    """
    selected = set(selected_paths)
    modified = files.set_index("path")["modified_ts"]
    return [
        ops.ApprovedAction(
            path=a.path,
            action=a.action,
            size_bytes=a.size_bytes,
            modified_ts=float(modified[a.path]),
            expected_benefit=a.benefit_bytes,
        )
        for a in plan.actions
        if a.path in selected
    ]


class Executor:
    def __init__(
        self,
        *,
        dry_run: bool = True,
        journal: Journal | None = None,
        archive_dir: str | None = None,
        move_destination: str | None = None,
        safety: SafetyConfig | None = None,
        trash: ops.Trash | None = None,
    ) -> None:
        self.dry_run = dry_run
        self.journal = journal
        self.archive_dir = archive_dir
        self.move_destination = move_destination
        self.safety = safety or SafetyConfig()
        self._trash = trash
        self._prefixes = protected_prefixes(self.safety)

    def run(
        self,
        actions: Sequence[ApprovedAction],
        *,
        progress: Progress | None = None,
        cancel: threading.Event | None = None,
    ) -> ExecutionReport:
        report = ExecutionReport(
            dry_run=self.dry_run,
            journal_path=str(self.journal.path) if self.journal else None,
        )
        for done, item in enumerate(actions, start=1):
            if cancel is not None and cancel.is_set():
                report.cancelled = True
                log.info("cancelled by user before %s", item.path)
                break
            result = self._run_one(item)
            report.results.append(result)
            log.log(
                logging.WARNING if result.status == "failed" else logging.INFO,
                "%s %s %s: %s (%s %d bytes%s)",
                result.status.upper(), result.action.value, result.path, result.message,
                "would free" if self.dry_run else "freed", result.bytes_freed,
                f", -> {result.artifact}" if result.artifact else "",
            )  # fmt: skip
            if self.journal:
                self.journal.record(
                    dry_run=self.dry_run,
                    path=result.path,
                    action=result.action.value,
                    status=result.status,
                    message=result.message,
                    size_bytes=item.size_bytes,
                    bytes_freed=result.bytes_freed,
                    artifact=result.artifact,
                )
            if progress:
                progress(done, len(actions), item.path)
        return report

    def _run_one(self, item: ApprovedAction) -> OperationResult:
        try:
            self._preflight(item)
        except ops.OperationError as exc:
            return OperationResult(item.path, item.action, "skipped", str(exc))
        if self.dry_run:
            return OperationResult(
                item.path, item.action, "dry-run", self._describe(item), item.expected_benefit
            )
        try:
            outcome = self._dispatch(item)
        except ops.OperationError as exc:
            return OperationResult(item.path, item.action, "failed", str(exc))
        except OSError as exc:
            return OperationResult(item.path, item.action, "failed", f"{type(exc).__name__}: {exc}")
        return OperationResult(
            item.path, item.action, "done", outcome.message, outcome.bytes_freed, outcome.artifact
        )

    def _preflight(self, item: ApprovedAction) -> None:
        if item.action is Action.KEEP:
            raise ops.OperationError("KEEP needs no operation")
        normalised = os.path.normcase(os.path.normpath(item.path))
        if normalised.startswith(self._prefixes):
            raise ops.OperationError("refused: file is under a protected location")
        st = ops.check_unchanged(item)
        if self.safety.protect_system_files and (
            getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_SYSTEM
        ):
            raise ops.OperationError("refused: file has the system attribute")
        if item.action is Action.MOVE and not self.move_destination:
            raise ops.OperationError("no MOVE destination configured")

    def _dispatch(self, item: ApprovedAction) -> ops.OperationOutcome:
        if item.action is Action.DELETE:
            return ops.delete(item, self._trash or _send_to_trash)
        if item.action is Action.ARCHIVE:
            return ops.archive(item, self.archive_dir)
        if item.action is Action.COMPRESS:
            return ops.compress(item)
        if item.action is Action.MOVE:
            return ops.move(item, self.move_destination)
        raise ops.OperationError(f"unsupported action {item.action}")

    def _describe(self, item: ApprovedAction) -> str:
        return {
            Action.DELETE: "would send to the Recycle Bin",
            Action.ARCHIVE: "would zip, verify, then remove the original",
            Action.COMPRESS: "would apply NTFS compression in place",
            Action.MOVE: f"would copy to {self.move_destination}, verify, then remove",
        }[item.action]


def _send_to_trash(path: str) -> None:
    from send2trash import send2trash

    send2trash(path)
