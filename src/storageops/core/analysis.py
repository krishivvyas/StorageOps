"""Run the whole Phase 2 pipeline (risk, safety, actions, heuristic) on a file table."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from storageops.config import Config
from storageops.core.actions import REMOVAL_ACTIONS, ActionTables, build_action_tables
from storageops.core.heuristic import heuristic_scores
from storageops.core.risk import assess_risk
from storageops.core.safety import assess_protection
from storageops.models import Action

_REMOVAL_COLUMNS = [a.value for a in REMOVAL_ACTIONS]


@dataclass(frozen=True)
class StorageAnalysis:
    """Per-file risk, protection and action matrices. All frames share one RangeIndex."""

    files: pd.DataFrame
    risk: pd.DataFrame
    protection: pd.DataFrame
    tables: ActionTables
    heuristic: pd.DataFrame

    @property
    def candidates(self) -> pd.Series:
        """True for files with at least one allowed action other than KEEP."""
        return self.tables.allowed[_REMOVAL_COLUMNS].any(axis=1)

    def best_actions(self) -> pd.DataFrame:
        """Highest-heuristic action per file (KEEP when nothing else is allowed)."""
        scores = self.heuristic[_REMOVAL_COLUMNS]
        best = scores.idxmax(axis=1) if len(scores) else pd.Series(dtype="object")
        has_option = np.isfinite(scores.max(axis=1))
        best = best.where(has_option, Action.KEEP.value)
        rows = np.arange(len(best))
        columns = self.tables.benefit.columns.get_indexer(best)
        return pd.DataFrame(
            {
                "best_action": best.astype("string"),
                "best_score": np.where(has_option, scores.max(axis=1), 0.0),
                "best_benefit": self.tables.benefit.to_numpy()[rows, columns],
                "best_cost": self.tables.cost.to_numpy()[rows, columns],
            },
            index=self.files.index,
        )

    def action_summary(self) -> pd.DataFrame:
        """Per removal action: how many files allow it and how many bytes it could free."""
        allowed = self.tables.allowed[_REMOVAL_COLUMNS]
        benefit = self.tables.benefit[_REMOVAL_COLUMNS].where(allowed, 0)
        return pd.DataFrame(
            {
                "allowed_files": allowed.sum(),
                "potential_bytes": benefit.sum(),
                "mean_cost": self.tables.cost[_REMOVAL_COLUMNS].where(allowed).mean(),
            }
        ).rename_axis("action")

    def to_frame(self) -> pd.DataFrame:
        """One wide, explainable table for display or export."""
        keep = ["path", "name", "category", "size_bytes", "age_days", "duplicate_group"]
        parts = [self.files[keep], self.risk, self.protection]
        for prefix, frame in (
            ("benefit", self.tables.benefit),
            ("cost", self.tables.cost),
            ("allowed", self.tables.allowed),
            ("h", self.heuristic),
        ):
            parts.append(frame[_REMOVAL_COLUMNS].add_prefix(f"{prefix}_"))
        parts.append(self.best_actions())
        return pd.concat(parts, axis=1)


def analyze(
    files: pd.DataFrame, config: Config | None = None, *, move_destination: str | None = None
) -> StorageAnalysis:
    """Score every file of a Phase 1 dataset. ``move_destination`` enables MOVE."""
    config = config or Config()
    files = files.reset_index(drop=True)
    risk = assess_risk(files, config.risk)
    protection = assess_protection(files, config.safety)
    tables = build_action_tables(
        files,
        risk["risk"],
        protection["protected"],
        config.actions,
        max_action_risk=config.safety.max_action_risk,
        move_destination=move_destination,
    )
    heuristic = heuristic_scores(files, risk["risk"], tables, config.heuristic, config.actions)
    return StorageAnalysis(files, risk, protection, tables, heuristic)
