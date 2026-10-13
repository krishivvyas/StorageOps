"""Action model: the benefit b(f, a), the cost c(f, a) and the eligibility of every
(file, action) pair.

Benefit (bytes freed on the source volume), with s = size and r = compression ratio
of the file's category:

    KEEP      0
    DELETE    s
    ARCHIVE   s * (1 - r)              strong compressor (zip/LZMA class)
    COMPRESS  s * (1 - r) * e          transparent in-place compression; e < 1 is its efficiency
    MOVE      s                        only to a destination on another volume

Cost (the path-cost term the planners minimise):

    c(f, KEEP) = 0
    c(f, a)    = severity(a) * R(f) + penalty(a)

severity scales the file's risk by how drastic the action is: DELETE 1.0, ARCHIVE 0.4,
MOVE 0.25, COMPRESS 0.1. penalty is a flat charge per touched file, which discourages
unnecessary actions.

An action is allowed only if: the file is not protected, severity(a) * R(f) <= max_action_risk,
it frees at least min_benefit_bytes, and (for ARCHIVE / COMPRESS) it saves at least
min_compression_saving of the file. Disallowed pairs cost +inf.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np
import pandas as pd

from storageops.config import ActionConfig
from storageops.models import Action

ACTIONS: tuple[Action, ...] = tuple(Action)
"""Canonical action order: the column order of every action matrix (KEEP first)."""
ACTION_INDEX: dict[Action, int] = {a: i for i, a in enumerate(ACTIONS)}
REMOVAL_ACTIONS: tuple[Action, ...] = tuple(a for a in ACTIONS if a is not Action.KEEP)
_COLUMNS = [a.value for a in ACTIONS]


@dataclass(frozen=True)
class ActionTables:
    """Per-file action matrices: rows align with the file table, columns = ``ACTIONS``."""

    benefit: pd.DataFrame  # int64 bytes; the physical potential, even when not allowed
    cost: pd.DataFrame  # float; +inf where not allowed
    allowed: pd.DataFrame  # bool


def volume_of(path: str) -> str:
    """Drive/volume identifier of ``path`` (``"c:"``). Empty on POSIX, where all paths share one."""
    return os.path.splitdrive(path)[0].casefold()


def build_action_tables(
    files: pd.DataFrame,
    risk: pd.Series,
    protected: pd.Series,
    config: ActionConfig | None = None,
    *,
    max_action_risk: float = 1.0,
    move_destination: str | None = None,
) -> ActionTables:
    config = config or ActionConfig()
    index = files.index
    size = files["size_bytes"].astype("int64")
    category = files["category"].astype(str)
    archive_saving = 1.0 - category.map(config.compression_ratio).astype(float)
    compress_saving = archive_saving * config.inplace_compression_efficiency

    if move_destination:
        destination = volume_of(os.path.abspath(move_destination))
        can_move = files["path"].map(lambda p: volume_of(p) != destination)
    else:
        can_move = pd.Series(False, index=index)

    benefit = pd.DataFrame(0, index=index, columns=_COLUMNS, dtype="int64")
    benefit[Action.DELETE.value] = size
    benefit[Action.ARCHIVE.value] = np.floor(size * archive_saving).astype("int64")
    benefit[Action.COMPRESS.value] = np.floor(size * compress_saving).astype("int64")
    benefit[Action.MOVE.value] = size.where(can_move, 0)

    allowed = pd.DataFrame(True, index=index, columns=_COLUMNS)
    cost = pd.DataFrame(0.0, index=index, columns=_COLUMNS)
    for action in REMOVAL_ACTIONS:
        severity = config.severity[action.value]
        allowed[action.value] = (
            ~protected.astype(bool)
            & (risk * severity <= max_action_risk)
            & (benefit[action.value] >= config.min_benefit_bytes)
        )
        cost[action.value] = severity * risk + config.penalty[action.value]
    allowed[Action.ARCHIVE.value] &= archive_saving >= config.min_compression_saving
    allowed[Action.COMPRESS.value] &= compress_saving >= config.min_compression_saving
    allowed[Action.MOVE.value] &= can_move

    return ActionTables(benefit=benefit, cost=cost.where(allowed, np.inf), allowed=allowed)
